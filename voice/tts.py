from __future__ import annotations

import asyncio
import os
import tempfile
from typing import AsyncIterator


class TextToSpeech:
    """
    Text-to-speech with two engine options:
      edge    — Microsoft Edge neural TTS (free, high quality, needs internet)
      pyttsx3 — fully offline fallback (robotic but zero latency)

    For streaming use, call speak_stream(sentence_iter) which overlaps
    audio generation and playback: sentence N+1 is generated while N plays.
    """

    def __init__(self, config: dict):
        self.engine = config.get("engine", "edge")
        self.voice = config.get("voice", "en-US-JennyNeural")
        self.rate = config.get("rate", "+0%")
        self.pitch = config.get("pitch", "+0Hz")
        self._pyttsx_engine = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def speak(self, text: str) -> None:
        """Speak a single text block (blocking until done)."""
        text = text.strip()
        if not text:
            return
        if self.engine == "edge":
            audio = await self._generate_mp3_bytes(text)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._play_mp3_bytes_sync, audio)
        else:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._speak_pyttsx3, text)

    async def speak_stream(self, sentences: AsyncIterator[str]) -> None:
        """
        Accept an async iterator of sentences and play them with minimal gaps.

        Generation of sentence N+1 overlaps with playback of sentence N.
        A queue of size 2 ensures we're always one sentence ahead.
        """
        if self.engine != "edge":
            async for sentence in sentences:
                await self.speak(sentence)
            return

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)

        async def producer() -> None:
            async for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                audio = await self._generate_mp3_bytes(sentence)
                await queue.put(audio)
            await queue.put(None)  # sentinel

        async def consumer() -> None:
            while True:
                audio = await queue.get()
                if audio is None:
                    break
                await loop.run_in_executor(None, self._play_mp3_bytes_sync, audio)

        await asyncio.gather(producer(), consumer())

    # ------------------------------------------------------------------
    # Edge TTS internals
    # ------------------------------------------------------------------

    async def _generate_mp3_bytes(self, text: str) -> bytes:
        """Stream audio bytes from edge-tts without writing to disk first."""
        import edge_tts
        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
        data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                data.extend(chunk["data"])
        return bytes(data)

    def _play_mp3_bytes_sync(self, data: bytes) -> None:
        """Write MP3 bytes to a temp file and play it synchronously."""
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(data)
            tmp = f.name
        try:
            self._play_audio_file(tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _play_audio_file(self, path: str) -> None:
        import subprocess
        import shutil

        if shutil.which("afplay"):          # macOS
            subprocess.run(["afplay", path], check=True)
        elif shutil.which("aplay"):         # Linux / RPi
            if shutil.which("ffmpeg"):
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    wav_path = f.name
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", path, wav_path],
                        check=True, capture_output=True,
                    )
                    subprocess.run(["aplay", "-q", wav_path], check=True)
                finally:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
            else:
                raise RuntimeError("ffmpeg required on Linux to play edge-tts audio")
        else:
            import soundfile as sf
            import sounddevice as sd
            audio_data, samplerate = sf.read(path)
            sd.play(audio_data, samplerate)
            sd.wait()

    # ------------------------------------------------------------------
    # pyttsx3 fallback
    # ------------------------------------------------------------------

    def _speak_pyttsx3(self, text: str) -> None:
        if self._pyttsx_engine is None:
            import pyttsx3
            self._pyttsx_engine = pyttsx3.init()
        self._pyttsx_engine.say(text)
        self._pyttsx_engine.runAndWait()
