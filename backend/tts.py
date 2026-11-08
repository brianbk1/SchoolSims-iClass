import hashlib
import os
import tempfile
from pathlib import Path

TTS_SUPPORTED_LANGS = {"en", "es", "ar", "zh", "ur"}

# On Vercel the project filesystem is read-only; only the temp dir is writable
# (and it is per-instance and short-lived). Locally we keep the original cache dir.
if os.getenv("VERCEL"):
    CACHE_DIR = Path(tempfile.gettempdir()) / "tts_cache"
else:
    CACHE_DIR = Path(__file__).parent / "tts_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _api_key() -> str | None:
    return os.getenv("ELEVEN_LABS_API_KEY") or os.getenv("ELEVENLABS_API_KEY")


def tts_enabled(lang: str) -> bool:
    """TTS is on only when a key is configured and the language is supported."""
    return bool(_api_key()) and lang in TTS_SUPPORTED_LANGS


def get_audio(text: str, lang: str = "en") -> bytes | None:
    """Return MP3 bytes for text, using the file cache; calls ElevenLabs on a miss."""
    if not tts_enabled(lang) or not text.strip():
        return None

    voice_id = os.getenv("ELEVEN_LABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
    model_id = os.getenv("ELEVEN_LABS_MODEL", "eleven_v4")

    key = hashlib.sha256(f"{text}{voice_id}{model_id}".encode()).hexdigest()
    path = CACHE_DIR / f"{key}.mp3"
    if path.exists():
        return path.read_bytes()

    from elevenlabs import ElevenLabs  # imported lazily so the app starts without it

    client = ElevenLabs(api_key=_api_key())
    audio = client.text_to_speech.convert(
        text=text,
        voice_id=voice_id,
        model_id=model_id,
        voice_settings={"stability": 0.5, "similarity_boost": 0.75},
    )
    data = b"".join(audio)
    try:
        path.write_bytes(data)
    except OSError:
        pass  # caching is best-effort
    return data
