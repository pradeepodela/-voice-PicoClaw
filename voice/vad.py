from __future__ import annotations

import collections
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import webrtcvad


class VADRecorder:
    """
    Records audio using webrtcvad with two guards against noise false-positives:

    Guard 1 — Onset window (300ms default):
        webrtcvad must classify >= `onset_ratio` of a 300ms rolling window as
        speech before recording starts. A short noise burst fills only 1-2 of
        the 10 frames and never triggers.

    Guard 2 — RMS energy gate:
        After recording, the clip's RMS energy must exceed `min_rms`. Clips that
        slipped through VAD but contain mostly silence are discarded here before
        ever reaching the STT API.
    """

    def __init__(self, config: dict):
        self.sample_rate: int = config.get("sample_rate", 16000)
        self.frame_ms: int = config.get("frame_duration_ms", 30)
        self.aggressiveness: int = config.get("aggressiveness", 3)
        self.silence_duration: float = config.get("silence_duration", 1.5)
        self.min_speech_duration: float = config.get("min_speech_duration", 0.5)
        self.min_rms: float = config.get("min_rms", 0.015)

        self.frame_samples = int(self.sample_rate * self.frame_ms / 1000)
        self.vad = webrtcvad.Vad(self.aggressiveness)

        # Onset ring buffer — 300ms window, need 75% speech frames to trigger
        onset_ms: int = config.get("onset_window_ms", 300)
        ring_size = max(1, onset_ms // self.frame_ms)
        self._ring: collections.deque = collections.deque(maxlen=ring_size)
        self._onset_ratio: float = config.get("onset_ratio", 0.75)

        # Silence frames needed to end an utterance
        self._silence_frames = int(self.silence_duration * 1000 / self.frame_ms)

    def record(self, timeout: float = 15.0, status_cb: Optional[Callable] = None) -> Optional[np.ndarray]:
        """
        Block until a valid utterance is captured.
        Returns float32 mono array at self.sample_rate, or None if nothing detected.
        """
        recorded: list[np.ndarray] = []
        speaking = False
        silence_count = 0
        total_frames = 0
        max_frames = int(timeout * 1000 / self.frame_ms)

        def _to_pcm16(frame: np.ndarray) -> bytes:
            return (frame * 32768).astype(np.int16).tobytes()

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.frame_samples,
        ) as stream:
            while total_frames < max_frames:
                data, _ = stream.read(self.frame_samples)
                frame = data.flatten()

                try:
                    is_speech = self.vad.is_speech(_to_pcm16(frame), self.sample_rate)
                except Exception:
                    is_speech = False

                self._ring.append(is_speech)

                if not speaking:
                    # Only trigger after sustained speech fills the onset window
                    if len(self._ring) == self._ring.maxlen:
                        ratio = sum(self._ring) / len(self._ring)
                        if ratio >= self._onset_ratio:
                            speaking = True
                            silence_count = 0
                            if status_cb:
                                status_cb("recording")
                else:
                    recorded.append(frame.copy())

                    if is_speech:
                        silence_count = 0
                    else:
                        silence_count += 1

                    if silence_count >= self._silence_frames:
                        break

                total_frames += 1

        if not recorded:
            return None

        audio = np.concatenate(recorded)

        # Guard 1: minimum speech duration
        min_samples = int(self.min_speech_duration * self.sample_rate)
        if len(audio) < min_samples:
            return None

        # Guard 2: RMS energy — rejects noise that fooled VAD
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms < self.min_rms:
            return None

        return audio

    def wait_for_wake_word(self, wake_phrase: str, stt_fn: Callable, timeout: float = 30.0) -> bool:
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            audio = self.record(timeout=5.0)
            if audio is None:
                continue
            if wake_phrase.lower() in stt_fn(audio).lower():
                return True
        return False
