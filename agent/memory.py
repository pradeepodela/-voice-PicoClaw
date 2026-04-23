import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm_client import LLMClient


class Memory:
    """
    Two-tier memory:
      1. Short-term: rolling window of recent messages (in-context)
      2. Long-term: summaries of older conversations persisted to disk

    When the short-term buffer exceeds `summarize_threshold`, the oldest
    half is summarized via the LLM and stored as a compressed summary message.
    """

    def __init__(self, config: dict, llm_client: "LLMClient"):
        self.max_short_term: int = config.get("max_short_term", 20)
        self.summarize_threshold: int = config.get("summarize_threshold", 16)
        self.persist_path = Path(config.get("persist_path", "data/memory.json"))
        self.llm = llm_client

        self.messages: list[dict] = []       # active conversation window
        self.summaries: list[dict] = []      # compressed older turns

        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, role: str, content: str) -> None:
        self.messages.append(
            {"role": role, "content": content, "ts": datetime.now().isoformat()}
        )

    def get_context(self) -> list[dict]:
        """Return messages suitable for the LLM (no internal 'ts' field)."""
        result = []
        # Inject summary block as a system note if we have one
        if self.summaries:
            combined = "\n\n".join(s["text"] for s in self.summaries)
            result.append(
                {
                    "role": "system",
                    "content": f"[Memory summary of earlier conversation]\n{combined}",
                }
            )
        for m in self.messages:
            result.append({"role": m["role"], "content": m["content"]})
        return result

    async def maybe_compress(self) -> None:
        """Summarize oldest messages when the buffer is getting large."""
        if len(self.messages) < self.summarize_threshold:
            return

        # Take the oldest half, summarize, replace with a compact block
        cutoff = len(self.messages) // 2
        old = self.messages[:cutoff]
        self.messages = self.messages[cutoff:]

        raw = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in old
        )
        summary_text = await self.llm.summarize(raw)
        self.summaries.append(
            {"text": summary_text, "ts": datetime.now().isoformat()}
        )
        self._save()

    def save(self) -> None:
        self._save()

    def clear(self) -> None:
        self.messages = []
        self.summaries = []
        self._save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text())
            self.summaries = data.get("summaries", [])
            # Only reload the last N messages to avoid stale context
            all_messages = data.get("messages", [])
            self.messages = all_messages[-self.max_short_term:]
        except (json.JSONDecodeError, KeyError):
            pass

    def _save(self) -> None:
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        self.persist_path.write_text(
            json.dumps(
                {"messages": self.messages, "summaries": self.summaries},
                indent=2,
            )
        )
