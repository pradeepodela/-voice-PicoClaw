from __future__ import annotations

import asyncio
import shutil

from agent.tool_registry import tool

_TIMEOUT = 90  # seconds — picoclaw may do web searches which take time


def _find_binary() -> str | None:
    return shutil.which("picoclaw")


@tool(
    name="picoclaw_agent",
    description=(
        "Delegate a complex agentic task to PicoClaw — an ultra-lightweight AI agent "
        "that can browse the web, run shell commands, read/write files, and chain "
        "multi-step reasoning. Use this when the user asks for: web searches, "
        "current news/events, running code snippets, system info, or any task "
        "that benefits from multiple tool calls. Returns PicoClaw's final answer."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "The full task or question to send to PicoClaw. "
                    "Be specific — it runs independently with no prior context."
                ),
            }
        },
        "required": ["task"],
    },
)
async def run_picoclaw(task: str) -> str:
    binary = _find_binary()
    if not binary:
        return (
            "PicoClaw is not installed. "
            "Run `bash scripts/setup_picoclaw.sh` to install it, then try again."
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "agent", "-m", task,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_TIMEOUT
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return f"PicoClaw timed out after {_TIMEOUT}s."
    except FileNotFoundError:
        return "PicoClaw binary not found. Run `bash scripts/setup_picoclaw.sh`."
    except Exception as e:
        return f"Error running PicoClaw: {e}"

    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()

    if proc.returncode != 0 and not out:
        return f"PicoClaw error (exit {proc.returncode}): {err or 'no output'}"

    return out or err or "PicoClaw returned no output."
