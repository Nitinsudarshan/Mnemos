"""Voice input/output for the existing retrieval + LLM pipeline.

Step 4 of the Mnemos build: Whisper STT as an input method feeding the same
pipeline built in steps 1-3 (retrieval + grounded ask), plus Piper TTS for
talkback. No live microphone capture yet, no hotkey — this step works from
audio *files* passed on the command line, verified from the terminal, same
as every prior step. Live mic capture and the push-to-talk/ambient hotkey
belong to step 5 (Tauri shell), since that's where the OS-level hotkey and
continuous audio stream naturally live — bolting mic capture onto the CLI
now would just get thrown away when the shell arrives. Flagging this as a
deliberate scope choice, not an assumption to skip past.

Design decisions (justified per project convention — not defaults):

- STT: faster-whisper. It's pure Python (CTranslate2 backend, no separate
  binary to build or keep on PATH) and decodes most common audio formats
  itself via bundled FFmpeg libraries (through PyAV) — so a person can pass
  a .wav, .mp3, or .m4a file without installing FFmpeg separately.
- STT model size: configurable via MNEMOS_WHISPER_MODEL (default "base").
  "base" is a reasonable default for short spoken questions on CPU — fast
  enough to feel responsive, accurate enough for clear speech. Override to
  "small" or "medium" if accuracy matters more than latency on your machine.
- TTS: Piper. Fully offline, small voice models (tens of MB), and — unlike
  Whisper — it has no built-in default voice, so MNEMOS_PIPER_MODEL and
  MNEMOS_PIPER_CONFIG must point at files you download once yourself. This
  is a deliberate no-default: guessing a voice model path that doesn't
  exist on your machine would fail confusingly, so this fails clearly
  instead, with instructions, if you haven't set it up yet.
- Output format: plain WAV, written by hand from Piper's raw int16 audio
  via the standard `wave` module — no extra audio-encoding dependency for
  a format this simple.
"""
from __future__ import annotations

import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend import llm

DEFAULT_WHISPER_MODEL = "base"

_whisper_model = None
_piper_voice = None


class VoiceConfigError(RuntimeError):
    """Raised when a required voice model/config isn't set up correctly."""


# ---------------------------------------------------------------------------
# STT (Whisper)
# ---------------------------------------------------------------------------

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        model_size = os.environ.get("MNEMOS_WHISPER_MODEL", DEFAULT_WHISPER_MODEL)
        _whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _whisper_model


def transcribe_audio(audio_path, model=None) -> str:
    """Transcribe an audio file (wav/mp3/m4a/etc.) to text."""
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    model = model or get_whisper_model()
    segments, _info = model.transcribe(str(path))
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text


# ---------------------------------------------------------------------------
# TTS (Piper)
# ---------------------------------------------------------------------------

def get_piper_voice():
    global _piper_voice
    if _piper_voice is None:
        model_path = os.environ.get("MNEMOS_PIPER_MODEL")
        if not model_path or not Path(model_path).exists():
            raise VoiceConfigError(
                "MNEMOS_PIPER_MODEL is not set or the file doesn't exist. "
                "Download a voice (e.g. en_US-lessac-medium) with:\n"
                "  python -m piper.download_voices en_US-lessac-medium "
                "--download-dir <some folder>\n"
                "then set MNEMOS_PIPER_MODEL to the resulting .onnx path "
                "(the matching .onnx.json config must sit alongside it, "
                "or set MNEMOS_PIPER_CONFIG explicitly)."
            )
        config_path = os.environ.get("MNEMOS_PIPER_CONFIG")  # optional; defaults to model_path + ".json"

        from piper import PiperVoice

        _piper_voice = PiperVoice.load(model_path, config_path=config_path)
    return _piper_voice


def synthesize_to_wav(text: str, output_path, voice=None) -> Path:
    """Synthesize text to speech and write it as a WAV file."""
    voice = voice or get_piper_voice()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    chunks = list(voice.synthesize(text))
    if not chunks:
        raise VoiceConfigError(f"Piper produced no audio for text: {text!r}")

    first = chunks[0]
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(first.sample_channels)
        wav_file.setsampwidth(first.sample_width)
        wav_file.setframerate(first.sample_rate)
        for chunk in chunks:
            wav_file.writeframes(chunk.audio_int16_bytes)

    return output_path


# ---------------------------------------------------------------------------
# Combined voice-in, voice-out pipeline
# ---------------------------------------------------------------------------

@dataclass
class VoiceAskResult:
    transcript: str
    answer_text: str
    answer_audio_path: Optional[Path]
    sources: list  # list[retrieval.SearchResult]


def voice_ask(
    audio_path,
    output_wav_path=None,
    k: int = 5,
    model: Optional[str] = None,
    speak: bool = True,
    whisper_model=None,
    piper_voice=None,
    embedder=None,
) -> VoiceAskResult:
    """Full pipeline: transcribe audio -> grounded ask() -> optionally
    synthesize the answer to speech."""
    transcript = transcribe_audio(audio_path, model=whisper_model)
    if not transcript:
        return VoiceAskResult(
            transcript="",
            answer_text="Couldn't make out any speech in that audio file.",
            answer_audio_path=None,
            sources=[],
        )

    result = llm.ask(transcript, k=k, model=model, embedder=embedder)

    answer_audio_path = None
    if speak:
        output_wav_path = output_wav_path or Path(audio_path).with_suffix(".answer.wav")
        answer_audio_path = synthesize_to_wav(result.answer, output_wav_path, voice=piper_voice)

    return VoiceAskResult(
        transcript=transcript,
        answer_text=result.answer,
        answer_audio_path=answer_audio_path,
        sources=result.sources,
    )
