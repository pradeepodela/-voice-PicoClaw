from __future__ import annotations

import asyncio
import re
import time
from typing import Callable, Awaitable

from agent.tool_registry import tool

# Set by app.py at startup — the TTS speak function
_speak: Callable[[str], Awaitable[None]] | None = None

# Delays below this go through asyncio (cron has ~1-second overhead + minute granularity issues)
_PICOCLAW_MIN_SECONDS = 30


def set_tts_callback(fn: Callable[[str], Awaitable[None]]) -> None:
    global _speak
    _speak = fn


def _make_job_name(message: str) -> str:
    """Derive a short, unique cron job name from the message."""
    slug = re.sub(r"[^a-z0-9]+", "_", message.lower())[:20].strip("_")
    return f"reminder_{slug}_{int(time.time()) % 100000}"


@tool(
    name="set_reminder",
    description=(
        "Schedule a spoken reminder after a delay. "
        "Use this whenever the user says 'remind me in X seconds/minutes/hours' or "
        "'tell me in X time to do Y'. The reminder will be spoken aloud via TTS "
        "even while a conversation is happening."
    ),
    parameters={
        "type": "object",
        "properties": {
            "seconds": {
                "type": "integer",
                "description": "Delay in seconds before the reminder fires (convert minutes/hours first)",
            },
            "message": {
                "type": "string",
                "description": "What to say aloud when the reminder fires",
            },
        },
        "required": ["seconds", "message"],
    },
)
async def set_reminder(seconds: int, message: str) -> str:
    mins, secs = divmod(seconds, 60)
    human = f"{mins}m {secs}s" if mins else f"{secs}s"

    if seconds >= _PICOCLAW_MIN_SECONDS:
        result = await _schedule_via_picoclaw(seconds, message)
        if result:
            return result

    # Short delay or PicoClaw unavailable — fall back to asyncio timer
    asyncio.create_task(_fire_reminder(seconds, message))
    return f"Reminder set — I'll say '{message}' in {human}."


async def _schedule_via_picoclaw(seconds: int, message: str) -> str | None:
    """
    Create a PicoClaw cron job that uses the speak skill to deliver the reminder.
    The cron message instructs the agent to speak via curl POST and then self-disable.
    Returns a human-readable confirmation string, or None on failure.
    """
    import shutil
    if not shutil.which("picoclaw"):
        return None

    job_name = _make_job_name(message)
    cron_message = (
        f"Use the speak skill to say: '{message}'. "
        f"Then immediately disable cron job named {job_name} so it does not repeat."
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "picoclaw", "cron", "add",
            "--every", str(seconds),
            "--name", job_name,
            "--message", cron_message,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            print(f"[reminder] picoclaw cron add failed: {err}")
            return None

        mins, secs = divmod(seconds, 60)
        human = f"{mins}m {secs}s" if mins else f"{secs}s"
        return (
            f"Reminder set via PicoClaw — will say '{message}' in {human}. "
            f"(Make sure the picoclaw gateway is running so the cron job fires.)"
        )

    except (asyncio.TimeoutError, FileNotFoundError, OSError) as e:
        print(f"[reminder] picoclaw cron add error: {e}")
        return None


async def _fire_reminder(seconds: int, message: str) -> None:
    await asyncio.sleep(max(0, seconds))
    print(f"\n[reminder] {message}", flush=True)
    if _speak:
        try:
            await _speak(message)
        except Exception as e:
            print(f"[reminder] TTS error: {e}")
