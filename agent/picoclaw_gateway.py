from __future__ import annotations

import asyncio
import os
import socket
import shutil
import subprocess

# Ports picoclaw gateway listens on — used to detect an existing instance
_GATEWAY_PORTS = (18790, 18800)


class GatewayManager:
    """
    Manages a picoclaw gateway subprocess in the background.

    Why: picoclaw cron jobs only fire when the gateway is running.
    One-shot `picoclaw agent -m` calls don't keep the gateway alive,
    so scheduled reminders never execute.

    This class starts `picoclaw gateway` as a background async subprocess,
    relays its stdout to our terminal (so reminders appear), and shuts it
    down cleanly when the agent exits.
    """

    def __init__(self, binary: str = "picoclaw", verbose: bool = False):
        self._binary = binary
        self._verbose = verbose
        self._proc: asyncio.subprocess.Process | None = None
        self._relay_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """
        Start the gateway only if one isn't already running.
        Returns True if the gateway is up (started by us or pre-existing).
        """
        if self.is_running():
            return True

        # Check if an external gateway is already running on the known ports.
        # This prevents the 409 Telegram conflict when the user manages the
        # gateway themselves in a separate terminal.
        if self._gateway_already_running():
            print("[gateway] picoclaw gateway already running — skipping auto-start")
            print("[gateway] tip: set auto_start_gateway: false in config.yaml to suppress this check")
            return True

        binary = shutil.which(self._binary)
        if not binary:
            print(f"[gateway] '{self._binary}' not found in PATH — gateway not started")
            return False

        self._proc = await asyncio.create_subprocess_exec(
            binary, "gateway",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._relay_task = asyncio.create_task(self._relay_output())
        print(f"[gateway] picoclaw gateway started (pid {self._proc.pid})")
        return True

    def _gateway_already_running(self) -> bool:
        """
        Return True if a picoclaw gateway process is already running.
        Checks both: an open TCP port (gateway is fully up) AND a running
        picoclaw process (gateway is starting up but not yet listening).
        Either condition is enough to skip launching a second instance.
        """
        # Port-level check: gateway is up and accepting connections
        for port in _GATEWAY_PORTS:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    return True
            except OSError:
                pass

        # Process-level check: catches the window where the process started
        # but hasn't bound its port yet, preventing a race-condition duplicate.
        binary_name = os.path.basename(self._binary)
        try:
            result = subprocess.run(
                ["pgrep", "-x", binary_name],
                capture_output=True,
                timeout=2.0,
            )
            if result.returncode == 0:
                pids = result.stdout.decode().split()
                # Exclude our own potential child from a previous start attempt
                own_pid = str(os.getpid())
                other_pids = [p for p in pids if p != own_pid]
                if other_pids:
                    print(
                        f"[gateway] picoclaw already running (pid {', '.join(other_pids)}) "
                        "— skipping auto-start to avoid Telegram 409 conflict"
                    )
                    return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass  # pgrep not available — fall through to port check only

        return False

    async def stop(self) -> None:
        """Gracefully shut down the gateway subprocess."""
        if self._relay_task and not self._relay_task.done():
            self._relay_task.cancel()

        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
            print("[gateway] picoclaw gateway stopped")

        self._proc = None
        self._relay_task = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # ------------------------------------------------------------------
    # Output relay — cron reminders appear in our terminal
    # ------------------------------------------------------------------

    async def _relay_output(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        try:
            async for line in self._proc.stdout:
                text = line.decode(errors="replace").rstrip()
                if text:
                    print(f"\n[picoclaw] {text}", flush=True)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
