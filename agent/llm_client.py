from __future__ import annotations

import os
from typing import Any, Optional
from openai import AsyncOpenAI


class LLMClient:
    def __init__(self, config: dict):
        self.config = config
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not set in environment")

        model_override = os.getenv("OPENROUTER_MODEL")
        self.model = model_override or config.get("model")
        self.max_tokens = config.get("max_tokens", 1024)
        self.temperature = config.get("temperature", 0.7)

        self.client = AsyncOpenAI(
            base_url=config.get("base_url", "https://openrouter.ai/api/v1"),
            api_key=api_key,
            default_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
                "X-Title": os.getenv("OPENROUTER_APP_NAME", "RPI-Agent"),
            },
        )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = False,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        return await self.client.chat.completions.create(**kwargs)

    async def summarize(self, text: str) -> str:
        """Summarize a block of conversation history to save context."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Summarize the following conversation history concisely, "
                        "preserving key facts, decisions, and context:\n\n" + text
                    ),
                }
            ],
            max_tokens=300,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""
