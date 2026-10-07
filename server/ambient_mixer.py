"""
Ambient noise — a looping background ambience played as a CONTINUOUS bed under the
agent's call so it sounds like a real person in a real room (a subtle office tone or a
busier call-centre floor) for the whole call, not just while the agent is speaking.

Mechanism: the server loads/tames/normalizes a looping ambience and exposes it as a
gain-scaled PCM16 mono @ 24 kHz bed via `bed_pcm()`. The browser fetches it once per
call (GET /api/ambient) and loops it at unity as a background layer for the WHOLE
call — independent of the agent's speech — so the room tone never cuts out between
turns. The agent's TTS plays over it untouched, so barge-in, hold music, idle timing
and byte counting are unaffected.

Enable with the AMBIENT_PRESET env var (none | office | call_center) and tune
loudness with AMBIENT_GAIN. Drop a 24 kHz / 16-bit / mono WAV named office.wav or
callcenter.wav in server/ambient_audio/ to use a real recording; otherwise a
synthetic ambience is generated so the feature works with no binary assets.
"""

from __future__ import annotations

import logging
import os
import wave
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger("outbound_rm.ambient")

try:
    import numpy as np

    _NUMPY_AVAILABLE = True
except ImportError:  # numpy is optional — ambient mixing simply stays disabled without it
    _NUMPY_AVAILABLE = False
    if not TYPE_CHECKING:
        np = None

SAMPLE_RATE = 24000       # Hz — must match the Voice Live output format
_AUDIO_DIR = Path(__file__).resolve().parent / "ambient_audio"
_DEFAULT_GAIN = 0.25      # default ambient volume (0..1 knob) when AMBIENT_GAIN is unset
_REFERENCE_RMS = 0.10     # ambient RMS at gain=1.0 (~-20 dBFS, a loud room); the knob scales this
_MIX_CEILING = 0.95       # hard-limit so the bed never overflows int16
_MAX_CREST = 4.0          # cap ambient peak at ~4x RMS so a loud transient can't clip out the gain


class AmbientMixer:
    """Loads or synthesizes a looping ambience and exposes it as a PCM16 bed to loop."""

    PRESETS = {
        "none": {"file": None, "texture": None},
        "office": {"file": "office.wav", "texture": "office"},
        "call_center": {"file": "CallCenter_AmbientNoise.wav", "texture": "call_center"},
    }

    def __init__(self, preset: str = "none", gain: float | None = None):
        if not _NUMPY_AVAILABLE:
            raise RuntimeError("numpy is required for ambient mixing")
        if preset not in self.PRESETS:
            raise ValueError(f"Unknown ambient preset {preset!r}; choose from {list(self.PRESETS)}")

        self.preset = preset
        self._noise_buffer = None
        self._loaded_from_file = False
        self._bed_pcm: bytes | None = None
        if preset != "none":
            self._noise_buffer = self._normalize(self._load_or_generate(preset))

        # Loudness: explicit arg > AMBIENT_GAIN env > default; clamped to [0, 1].
        raw_gain = gain if gain is not None else os.getenv("AMBIENT_GAIN", "")
        try:
            self._ambient_gain = float(raw_gain) if raw_gain not in (None, "") else _DEFAULT_GAIN
        except (TypeError, ValueError):
            self._ambient_gain = _DEFAULT_GAIN
        self._ambient_gain = max(0.0, min(self._ambient_gain, 1.0))

        logger.info(
            "AmbientMixer ready: preset=%s gain=%.3f source=%s samples=%d",
            preset,
            self._ambient_gain,
            "file" if self._loaded_from_file else "synthetic",
            0 if self._noise_buffer is None else len(self._noise_buffer),
        )

    # ── Buffer construction ──────────────────────────────────────────────

    def _load_or_generate(self, preset: str):
        spec = self.PRESETS[preset]
        path = _AUDIO_DIR / spec["file"] if spec.get("file") else None
        if path is not None and path.exists():
            try:
                buf = self._load_wav(path)
                self._loaded_from_file = True
                return buf
            except Exception:
                logger.exception("Failed to load ambient WAV %s; using synthetic ambience", path)
        return self._generate_synthetic(spec.get("texture") or "office")

    @staticmethod
    def _load_wav(path: Path):
        """Read a WAV file into a mono float32 array resampled to 24 kHz."""
        with wave.open(str(path), "rb") as wav:
            n_channels = wav.getnchannels()
            sampwidth = wav.getsampwidth()
            framerate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())

        if sampwidth == 2:
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 1:
            audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            raise ValueError(f"Unsupported WAV sample width: {sampwidth} bytes")

        if n_channels == 2:
            audio = audio.reshape(-1, 2).mean(axis=1)

        if framerate != SAMPLE_RATE and len(audio) > 1:
            new_len = int(round(len(audio) * SAMPLE_RATE / framerate))
            audio = np.interp(
                np.linspace(0.0, len(audio), new_len, endpoint=False),
                np.arange(len(audio)),
                audio,
            ).astype(np.float32)
        return np.ascontiguousarray(audio, dtype=np.float32)

    @staticmethod
    def _shaped_noise(n: int, exponent: float, rng, f_lo: float = 60.0, f_hi: float = 6500.0):
        """Band-limited 1/f^exponent noise (exp 1 = pink, 2 = brown) via spectral shaping.

        Energy is confined to the audible [f_lo, f_hi] band so that after unit-RMS
        normalisation the ambience is actually heard on ordinary speakers — pure
        brown noise would bury most of its energy in inaudible sub-bass rumble.
        """
        white = rng.standard_normal(n)
        spectrum = np.fft.rfft(white)
        freqs = np.fft.rfftfreq(n, d=1.0 / SAMPLE_RATE)  # Hz
        scale = np.zeros_like(freqs)
        band = (freqs >= f_lo) & (freqs <= f_hi)
        scale[band] = 1.0 / np.power(freqs[band], exponent / 2.0)  # shape amplitude; DC removed
        colored = np.fft.irfft(spectrum * scale, n=n)
        return colored.astype(np.float32)

    def _generate_synthetic(self, texture: str, duration_sec: float = 12.0):
        """Build a seamless loop of synthetic ambience for the given texture."""
        n = int(SAMPLE_RATE * duration_sec)
        rng = np.random.default_rng(seed=1234 if texture == "office" else 4321)
        if texture == "call_center":
            # Wider band + a slow swell to suggest a busy room of distant voices.
            base = self._shaped_noise(n, 1.0, rng, f_lo=150.0, f_hi=5000.0)
            murmur = self._shaped_noise(n, 1.0, rng, f_lo=300.0, f_hi=3400.0)  # speech band
            t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
            swell = 0.7 + 0.3 * np.sin(2.0 * np.pi * 0.1 * t)  # slow ebb/flow of the room
            buf = 0.7 * base + 0.6 * murmur * swell
        else:  # office / default — warm, steady room tone (pink noise)
            buf = self._shaped_noise(n, 1.0, rng, f_lo=80.0, f_hi=4000.0)
        return buf.astype(np.float32)

    @staticmethod
    def _normalize(buf, target_rms: float = 1.0):
        """Unit-RMS normalize, then soft-limit transients so `gain` maps to what's heard.

        A real recording can hide a loud transient (office.wav peaks ~61x its RMS);
        left alone it clips to full scale at every gain and flattens tuning. A tanh
        knee at _MAX_CREST tames the tail (leaving the bed ~linear) and the second
        normalize lifts the true bed, which the transient had been inflating.
        """
        if buf is None or len(buf) == 0:
            return buf
        b = buf.astype(np.float64)
        rms = float(np.sqrt(np.mean(np.square(b))))
        if rms > 1e-9:
            b = b / rms
        b = _MAX_CREST * np.tanh(b / _MAX_CREST)  # soft-limit peaks to ~_MAX_CREST x RMS
        rms = float(np.sqrt(np.mean(np.square(b))))
        if rms > 1e-9:
            b = b * (target_rms / rms)
        return np.ascontiguousarray(b, dtype=np.float32)

    # ── Continuous bed for the client to loop ─────────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        return self.preset != "none" and self._noise_buffer is not None and self._ambient_gain > 0.0

    def bed_pcm(self) -> bytes:
        """Return the whole gain-scaled ambience as PCM16 mono bytes (cached).

        The browser loops this at unity as a continuous background bed for the
        entire call, independent of the agent's speech. AMBIENT_GAIN is a 0..1
        volume knob: effective ambient RMS = gain * _REFERENCE_RMS.
        """
        if not self.is_enabled() or self._noise_buffer is None:
            return b""
        if self._bed_pcm is None:
            level = self._ambient_gain * _REFERENCE_RMS
            buf = np.clip(self._noise_buffer * level, -_MIX_CEILING, _MIX_CEILING)
            self._bed_pcm = (buf * 32767.0).astype(np.int16).tobytes()
        return self._bed_pcm


# ── Process-wide singleton (noise buffer + PCM bed built once, shared across calls) ──
_shared_mixer: "AmbientMixer | None" = None
_shared_key: "tuple[str, str] | None" = None


def get_ambient_mixer() -> "AmbientMixer | None":
    """Return the configured ambient mixer, or None when disabled/unavailable."""
    global _shared_mixer, _shared_key
    preset = (os.getenv("AMBIENT_PRESET", "none") or "none").strip().lower()
    gain_env = os.getenv("AMBIENT_GAIN", "").strip()
    if preset in ("", "none"):
        return None
    if not _NUMPY_AVAILABLE:
        logger.warning("AMBIENT_PRESET=%s but numpy is not installed — ambient noise disabled", preset)
        return None
    if preset not in AmbientMixer.PRESETS:
        logger.warning("Unknown AMBIENT_PRESET=%r — ambient noise disabled", preset)
        return None

    key = (preset, gain_env)
    if _shared_mixer is not None and _shared_key == key:
        return _shared_mixer
    try:
        mixer = AmbientMixer(preset=preset, gain=float(gain_env) if gain_env else None)
    except Exception:
        logger.exception("Failed to initialise AmbientMixer(preset=%s); ambient noise disabled", preset)
        return None
    if not mixer.is_enabled():
        return None
    _shared_mixer, _shared_key = mixer, key
    return mixer


def ambient_config_summary() -> str:
    """Cheap, buffer-free description of the ambient setting for a startup log."""
    preset = (os.getenv("AMBIENT_PRESET", "none") or "none").strip().lower()
    if preset in ("", "none"):
        return "Ambient noise: DISABLED (AMBIENT_PRESET=none)"
    if not _NUMPY_AVAILABLE:
        return f"Ambient noise: DISABLED (AMBIENT_PRESET={preset} but numpy is not installed)"
    if preset not in AmbientMixer.PRESETS:
        return f"Ambient noise: DISABLED (unknown AMBIENT_PRESET={preset})"
    gain = os.getenv("AMBIENT_GAIN", "").strip() or f"{_DEFAULT_GAIN} (default)"
    return f"Ambient noise: ENABLED (preset={preset}, gain={gain})"
