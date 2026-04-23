from __future__ import annotations

import asyncio
import re
import shutil
from typing import AsyncGenerator, AsyncIterator

from openai import APIStatusError

from .llm_client import LLMClient
from .memory import Memory
from .tool_registry import ToolRegistry

# Match a sentence-ending punctuation followed by whitespace.
# Lookbehind keeps the punctuation in the left chunk.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        memory: Memory,
        tools: ToolRegistry,
        system_prompt: str,
        agent_name: str = "RPI Agent",
        verbose: bool = False,
        picoclaw_primary: bool = False,
        picoclaw_binary: str = "picoclaw",
        picoclaw_timeout: int = 90,
        picoclaw_history_turns: int = 4,
    ):
        self.llm = llm
        self.memory = memory
        self.tools = tools
        self.system_prompt = system_prompt
        self.name = agent_name
        self.verbose = verbose
        self.picoclaw_primary = picoclaw_primary
        self._picoclaw_binary = picoclaw_binary
        self._picoclaw_timeout = picoclaw_timeout
        self._picoclaw_history_turns = picoclaw_history_turns

    # ------------------------------------------------------------------
    # PicoClaw primary-mode helpers
    # ------------------------------------------------------------------

    def _build_picoclaw_query(self, current_query: str) -> str:
        """
        Prepend recent conversation history so PicoClaw has context for
        follow-up questions. Excludes the current turn (already in memory).
        """
        max_msgs = self._picoclaw_history_turns * 2  # each turn = 1 user + 1 assistant
        history = [
            m for m in self.memory.messages[:-1]   # skip the current user turn
            if m["role"] in ("user", "assistant")
        ][-max_msgs:]

        if not history:
            return current_query

        lines = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
            for m in history
        )
        return f"[Conversation so far]\n{lines}\n\n[Current message]\n{current_query}"

    async def _call_picoclaw(self, query: str) -> str:
        """Build context-aware query and run picoclaw agent -m <query>."""
        binary = shutil.which(self._picoclaw_binary)
        if not binary:
            return (
                f"PicoClaw binary '{self._picoclaw_binary}' not found in PATH. "
                "Make sure PicoClaw is installed."
            )

        full_query = self._build_picoclaw_query(query)

        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "agent", "-m", full_query,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._picoclaw_timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return f"PicoClaw timed out after {self._picoclaw_timeout}s."
        except Exception as e:
            return f"Error calling PicoClaw: {e}"

        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        return out or err or "PicoClaw returned no output."

    async def _picoclaw_stream_sentences(self, query: str) -> AsyncGenerator[str, None]:
        """Call PicoClaw and yield its response as sentences (no true streaming — picoclaw is a subprocess)."""
        reply = await self._call_picoclaw(query)
        buffer = reply
        while True:
            m = _SENTENCE_END.search(buffer)
            if not m:
                break
            sentence = buffer[: m.start() + 1].strip()
            buffer = buffer[m.end() :]
            if sentence:
                yield sentence
        if buffer.strip():
            yield buffer.strip()
        if reply.strip():
            self.memory.add("assistant", reply.strip())
            self.memory.save()

    # ------------------------------------------------------------------
    # Public chat methods
    # ------------------------------------------------------------------

    async def chat(self, user_input: str) -> str:
        """
        Process a single user turn and return the agent's response text.
        Routes to PicoClaw or OpenRouter depending on picoclaw_primary flag.
        """
        self.memory.add("user", user_input)
        await self.memory.maybe_compress()

        if self.picoclaw_primary:
            if self.verbose:
                print("  [routing] PicoClaw primary")
            reply = await self._call_picoclaw(user_input)
            self.memory.add("assistant", reply)
            self.memory.save()
            return reply

        messages = [{"role": "system", "content": self.system_prompt}]
        messages += self.memory.get_context()

        tool_schemas = self.tools.all_schemas()
        max_tool_rounds = 5  # prevent runaway loops
        tools_supported = True  # flip to False if model rejects tool calling

        for _ in range(max_tool_rounds):
            send_tools = tool_schemas if (tool_schemas and tools_supported) else None
            try:
                response = await self.llm.chat(
                    messages=messages,
                    tools=send_tools,
                )
            except APIStatusError as e:
                # Model/provider doesn't support tool calling — retry plain
                if e.status_code == 404 and "tool" in str(e).lower():
                    tools_supported = False
                    if self.verbose:
                        print(f"  [warn] model doesn't support tools, retrying without")
                    response = await self.llm.chat(messages=messages, tools=None)
                else:
                    raise
            choice = response.choices[0]
            msg = choice.message

            # Model wants to call one or more tools
            if choice.finish_reason == "tool_calls" and msg.tool_calls:
                messages.append(msg.model_dump(exclude_unset=True))

                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    fn_args = tc.function.arguments

                    if self.verbose:
                        print(f"  [tool] {fn_name}({fn_args})")

                    result = await self.tools.call(fn_name, fn_args)

                    if self.verbose:
                        print(f"  [tool result] {result[:120]}")

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )
                # Loop back to let the model process tool results
                continue

            # Model returned a final text response
            reply = (msg.content or "").strip()
            self.memory.add("assistant", reply)
            self.memory.save()
            return reply

        # Fallback if we somehow exhausted tool rounds
        return "I ran into a loop trying to use tools. Please try rephrasing."

    async def stream_sentences(self, user_input: str) -> AsyncGenerator[str, None]:
        """
        Yield complete sentences as the response is generated.
        Routes to PicoClaw or OpenRouter depending on picoclaw_primary flag.
        """
        self.memory.add("user", user_input)
        await self.memory.maybe_compress()

        if self.picoclaw_primary:
            if self.verbose:
                print("  [routing] PicoClaw primary")
            async for sentence in self._picoclaw_stream_sentences(user_input):
                yield sentence
            return

        messages = [{"role": "system", "content": self.system_prompt}]
        messages += self.memory.get_context()

        tool_schemas = self.tools.all_schemas()
        tools_supported = True

        for _ in range(5):
            send_tools = tool_schemas if (tool_schemas and tools_supported) else None

            try:
                stream = await self.llm.chat(messages=messages, tools=send_tools, stream=True)
            except APIStatusError as e:
                if e.status_code == 404 and "tool" in str(e).lower():
                    tools_supported = False
                    if self.verbose:
                        print("  [warn] model doesn't support tools, retrying without")
                    stream = await self.llm.chat(messages=messages, tools=None, stream=True)
                else:
                    raise

            buffer = ""
            full_content = ""
            tc_accum: dict = {}   # {index: {id, name, args}}
            finish_reason = None

            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                delta = choice.delta

                # Reassemble streamed tool call argument chunks
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        i = tc.index
                        if i not in tc_accum:
                            tc_accum[i] = {"id": "", "name": "", "args": ""}
                        if tc.id:
                            tc_accum[i]["id"] += tc.id
                        if tc.function:
                            if tc.function.name:
                                tc_accum[i]["name"] += tc.function.name
                            if tc.function.arguments:
                                tc_accum[i]["args"] += tc.function.arguments

                # Yield complete sentences as content tokens arrive
                if delta.content:
                    buffer += delta.content
                    full_content += delta.content
                    while True:
                        m = _SENTENCE_END.search(buffer)
                        if not m:
                            break
                        sentence = buffer[: m.start() + 1].strip()
                        buffer = buffer[m.end() :]
                        if sentence:
                            yield sentence

            if finish_reason == "tool_calls" and tc_accum:
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["args"]},
                            }
                            for tc in tc_accum.values()
                        ],
                    }
                )
                for tc in tc_accum.values():
                    if self.verbose:
                        print(f"  [tool] {tc['name']}({tc['args'][:60]})")
                    result = await self.tools.call(tc["name"], tc["args"])
                    if self.verbose:
                        print(f"  [tool result] {result[:80]}")
                    messages.append(
                        {"role": "tool", "tool_call_id": tc["id"], "content": result}
                    )
                continue  # stream the follow-up answer

            # finish_reason == "stop" — flush remaining buffer and save
            if buffer.strip():
                yield buffer.strip()

            reply = full_content.strip()
            if reply:
                self.memory.add("assistant", reply)
                self.memory.save()
            return

        yield "I ran into an issue resolving tool calls. Please try again."

    def reset_session(self) -> None:
        """Clear in-session memory (summaries and history persist on disk)."""
        self.memory.messages = []
