from __future__ import annotations

import io
import os
import tempfile

import numpy as np
import scipy.io.wavfile as wav


class SpeechToText:
    """
    Speech-to-text with two engine options:

    groq  — Groq Whisper API (whisper-large-v3-turbo). Requires GROQ_API_KEY.
             Free tier: 7,200 seconds of audio per day. Very fast (~300ms).
             No local model download needed — ideal for RPi.

    local — faster-whisper running on-device. No API key, no internet.
             Models: tiny (~70MB), base (~140MB), small (~460MB).
    """

    def __init__(self, config: dict):
        self.engine = config.get("engine", "groq")
        self.language = config.get("language", "en")

        # Groq settings
        self.groq_model = config.get("groq_model", "whisper-large-v3-turbo")
        self._groq_client = None

        # Local faster-whisper settings
        self._model_size = config.get("model", "tiny")
        self._device = config.get("device", "cpu")
        self._compute_type = config.get("compute_type", "int8")
        self._local_model = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def transcribe(self, audio: np.ndarray) -> str:
        """Accept float32 mono numpy array at 16kHz, return transcribed text."""
        audio = _ensure_mono_float32(audio)
        if self.engine == "groq":
            text = self._transcribe_groq(audio)
        else:
            text = self._transcribe_local(audio)
        return _filter_hallucination(text)

    # ------------------------------------------------------------------
    # Groq Whisper
    # ------------------------------------------------------------------

    def _ensure_groq(self):
        if self._groq_client is None:
            from groq import Groq
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise ValueError("GROQ_API_KEY not set in environment")
            self._groq_client = Groq(api_key=api_key)

    def _transcribe_groq(self, audio: np.ndarray) -> str:
        self._ensure_groq()

        # Groq API requires a file — write numpy audio to an in-memory WAV
        buf = io.BytesIO()
        wav.write(buf, rate=16000, data=(audio * 32767).astype(np.int16))
        buf.seek(0)
        buf.name = "audio.wav"  # groq client reads the .name attribute

        transcription = self._groq_client.audio.transcriptions.create(
            file=buf,
            model=self.groq_model,
            language=self.language,
            response_format="text",
        )
        # response_format="text" returns a plain string directly
        return (transcription if isinstance(transcription, str) else transcription.text).strip()

    # ------------------------------------------------------------------
    # Local faster-whisper
    # ------------------------------------------------------------------

    def _ensure_local(self):
        if self._local_model is None:
            from faster_whisper import WhisperModel
            print(f"[STT] Loading whisper '{self._model_size}' model locally...")
            self._local_model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            print("[STT] Local model ready.")

    def _transcribe_local(self, audio: np.ndarray) -> str:
        self._ensure_local()
        segments, _ = self._local_model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(seg.text for seg in segments).strip()


def _ensure_mono_float32(audio: np.ndarray) -> np.ndarray:
    audio = audio.astype(np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio


# Whisper reliably hallucinates these phrases on silence or background noise.
# Normalise to lowercase + strip punctuation before checking.
_HALLUCINATIONS = {
    "thank you", "thank you for watching", "thanks for watching",
    "thanks", "you", "bye", "goodbye", "good bye",
    "please subscribe", "subscribe", "like and subscribe",
    "see you next time", "see you in the next video",
    "subtitles by", "captions by", "transcribed by",
    "", ".", "..", "...", "…",
}

_STRIP_CHARS = str.maketrans("", "", ".,!?…’‘\"'")


def _filter_hallucination(text: str) -> str:
    normalised = text.lower().strip().translate(_STRIP_CHARS)
    if normalised in _HALLUCINATIONS:
        return ""
    return text
