from __future__ import annotations

import asyncio
import json
from asyncio import StreamReader, StreamWriter
from typing import Callable, Awaitable


class ReminderServer:
    """
    Tiny HTTP server that listens for POST /remind from PicoClaw cron jobs.

    PicoClaw cron jobs can't push messages back to our voice agent directly,
    but they CAN run shell commands. We configure those commands to POST the
    reminder text here, and we speak it via TTS.

    Setup in PicoClaw — when creating a cron reminder, tell it to run:
        curl -s -X POST http://localhost:7700/remind -d "Your reminder message"
    or with JSON:
        curl -s -X POST http://localhost:7700/remind \\
             -H "Content-Type: application/json" \\
             -d '{"message": "Your reminder message"}'
    """

    def __init__(
        self,
        port: int = 7700,
        on_reminder: Callable[[str], Awaitable[None]] | None = None,
        verbose: bool = False,
    ):
        self.port = port
        self._on_reminder = on_reminder
        self._verbose = verbose
        self._server = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", self.port
        )
        print(f"[reminder] HTTP server listening on localhost:{self.port}")
        print(f"[reminder] PicoClaw cron command: "
              f'curl -s -X POST http://localhost:{self.port}/remind -d "your message"')

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: StreamReader, writer: StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.read(4096), timeout=5.0)
        except asyncio.TimeoutError:
            writer.close()
            return

        request = raw.decode(errors="replace")
        lines = request.split("\r\n")

        # Only handle POST /remind
        if not lines or not lines[0].startswith("POST /remind"):
            writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        # Parse body (after the blank line separating headers from body)
        body = ""
        if "\r\n\r\n" in request:
            body = request.split("\r\n\r\n", 1)[1].strip()

        # Accept plain text or {"message": "..."} JSON
        message = body
        try:
            data = json.loads(body)
            message = data.get("message") or data.get("text") or data.get("reminder") or body
        except (json.JSONDecodeError, AttributeError):
            pass

        message = message.strip()

        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
        writer.close()

        if not message:
            return

        print(f"\n[reminder] {message}", flush=True)
        if self._on_reminder:
            try:
                await self._on_reminder(message)
            except Exception as e:
                if self._verbose:
                    print(f"[reminder] TTS error: {e}")
