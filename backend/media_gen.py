"""Images and talking-character videos for AI-built sims, made from the hosted app.

Videos: HeyGen v3 (same pipeline as scripts/make_videos.py): the character's portrait
is framed on a 16:9 canvas, placed in front of the region's background, and speaks the
slide's script with a voice that matches the language and region. HeyGen renders for a
few minutes, so a video is a job: start it, then `refresh` until it is done, then the
mp4 is copied to Vercel Blob (HeyGen's own links expire) with WebVTT captions.

Images: OpenAI image API, stored in Vercel Blob (or the runtime store without Blob).

Settings: HEYGEN_API_KEY, BLOB_READ_WRITE_TOKEN (Vercel -> Storage -> Blob), OPENAI_API_KEY.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import struct
import time
from pathlib import Path

import httpx

import store

REPO = Path(__file__).resolve().parent.parent
HEYGEN = "https://api.heygen.com"
BLOB = "https://blob.vercel-storage.com"
ASSET_HASH = "heygen_assets"       # cache: what we uploaded to HeyGen -> asset id

# Voice language + accent words to look for, per locale (mirrors scripts/heygen/clips.json).
VOICE_SPECS = {
    "en": (["English"], []), "en-US": (["English"], ["United States", "US", "American"]),
    "en-GB": (["English"], ["United Kingdom", "UK", "British", "England"]),
    "es": (["Spanish"], ["Latin America", "Mexico"]), "es-ES": (["Spanish"], ["Spain", "Castilian", "España"]),
    "es-MX": (["Spanish"], ["Mexico", "México", "Mexican"]), "es-AR": (["Spanish"], ["Argentina", "Argentine"]),
    "es-CL": (["Spanish"], ["Chile", "Chilean"]), "pt-PT": (["Portuguese"], ["Portugal", "European"]),
    "eu": (["Basque"], []), "af": (["Afrikaans"], []), "he": (["Hebrew"], []),
    "zh": (["Chinese", "Mandarin"], ["Mandarin"]), "zh-HK": (["Cantonese", "Chinese"], ["Cantonese", "Hong Kong"]),
    "ne": (["Nepali"], []), "ur": (["Urdu"], ["Pakistan"]), "sw": (["Swahili"], ["Kenya"]),
    "sw-TZ": (["Swahili"], ["Tanzania"]), "ar": (["Arabic"], ["Gulf", "UAE", "Emirati"]),
    "ar-JO": (["Arabic"], ["Jordan", "Levantine"]),
}
# Locales without their own background borrow a close one (None = keep the photo's own).
BACKGROUND_FALLBACK = {"en": "en-US", "es": "es-MX", "zh": "zh-HK", "ar": None, "ar-JO": None}


def heygen_ready() -> bool:
    return bool(os.getenv("HEYGEN_API_KEY"))


def blob_token() -> str:
    """Vercel Blob read-write token. Vercel names it BLOB_READ_WRITE_TOKEN unless a custom
    prefix was chosen when connecting the store (e.g. MEDIA_READ_WRITE_TOKEN): accept any."""
    if os.getenv("BLOB_READ_WRITE_TOKEN"):
        return os.environ["BLOB_READ_WRITE_TOKEN"]
    for k, v in sorted(os.environ.items()):
        if k.endswith("READ_WRITE_TOKEN") and v.startswith("vercel_blob_rw_"):
            return v
    return ""


# Newer Vercel Blob connections use OIDC: no read-write token, just BLOB_STORE_ID plus a
# short-lived token Vercel sends with each request (x-vercel-oidc-token header) or sets
# as VERCEL_OIDC_TOKEN. The middleware in main.py hands the latest header value here.
_oidc_token = ""


def set_oidc_token(token: str | None) -> None:
    global _oidc_token
    if token:
        _oidc_token = token.strip()


def blob_store_id() -> str:
    sid = os.getenv("BLOB_STORE_ID", "").strip()
    if not sid:
        for k, v in sorted(os.environ.items()):
            if k.endswith("_STORE_ID") and v.strip().startswith("store_") and "BLOB" in k:
                sid = v.strip()
                break
    return sid[len("store_"):] if sid.startswith("store_") else sid


def blob_auth() -> dict[str, str]:
    """Headers that authorize a Blob upload: read-write token, else OIDC + store id."""
    tok = blob_token()
    if tok:
        return {"authorization": f"Bearer {tok}"}
    oidc = _oidc_token or os.getenv("VERCEL_OIDC_TOKEN", "").strip()
    sid = blob_store_id()
    if oidc and sid:
        return {"authorization": f"Bearer {oidc}", "x-vercel-blob-store-id": sid}
    return {}


def blob_ready() -> bool:
    # On Vercel the OIDC token arrives with requests, so a connected store id is enough.
    return bool(blob_token() or (blob_store_id() and (os.getenv("VERCEL") or _oidc_token or os.getenv("VERCEL_OIDC_TOKEN"))))


def blob_vars_seen() -> list[str]:
    """Names (never values) of settings that look like Blob storage, for messages."""
    return sorted(k for k in os.environ if "BLOB" in k or k.endswith("READ_WRITE_TOKEN"))


def images_ready() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


# ---------------------------------------------------------------------------
# Files that ship with the site (photos, backgrounds)
# ---------------------------------------------------------------------------

def site_file(path: str, base_url: str) -> bytes:
    """A file under public/ (e.g. '/assets/latin.png'). Read from disk when it's there
    (local / Codespace); on Vercel the function doesn't include public/, so fetch it."""
    if path.startswith(("http://", "https://")):  # e.g. a look's photo in Blob
        r = httpx.get(path, timeout=60, follow_redirects=True)
        r.raise_for_status()
        return r.content
    if path.startswith("/api/"):  # served by this app (photo kept in the runtime store)
        r = httpx.get(f"{base_url.rstrip('/')}{path}", timeout=60, follow_redirects=True)
        r.raise_for_status()
        return r.content
    local = REPO / "public" / path.lstrip("/")
    if local.exists():
        return local.read_bytes()
    r = httpx.get(f"{base_url.rstrip('/')}/{path.lstrip('/')}", timeout=60, follow_redirects=True)
    r.raise_for_status()
    return r.content


# Region backgrounds in public/backgrounds/ (made with ChatGPT; see scripts/heygen/BACKGROUNDS.md).
BACKGROUNDS = {"af", "en-GB", "en-US", "es-AR", "es-CL", "es-ES", "es-MX", "eu", "he", "ne", "pt-PT",
               "sw", "sw-TZ", "ur", "zh-HK"}


def background_for(lang: str) -> str | None:
    """Most specific background for a locale; None keeps the portrait's own backdrop."""
    for c in dict.fromkeys([lang, lang.split("-")[0]]):
        if c in BACKGROUNDS:
            return f"/backgrounds/{c}.jpg"
        if c in BACKGROUND_FALLBACK:
            fb = BACKGROUND_FALLBACK[c]
            return f"/backgrounds/{fb}.jpg" if fb else None
    return None


def frame_photo(data: bytes, person_height: float = 0.9) -> bytes:
    """Portrait on a 16:9 canvas at a natural video-call size (see scripts/make_videos.py)."""
    from PIL import Image

    W, H = 1920, 1080
    im = Image.open(io.BytesIO(data)).convert("RGB")
    scale = (H * person_height) / im.height
    im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    if im.width > W:
        im = im.resize((W, round(im.height * W / im.width)), Image.LANCZOS)
    edge = im.resize((1, 1)).getpixel((0, 0))
    canvas = Image.new("RGB", (W, H), edge)
    canvas.paste(im, ((W - im.width) // 2, H - im.height))
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=92)
    return out.getvalue()


# ---------------------------------------------------------------------------
# HeyGen
# ---------------------------------------------------------------------------

class HeyGen:
    def __init__(self, client: httpx.Client | None = None):
        self.c = client or httpx.Client(timeout=90)
        self.h = {"X-Api-Key": os.environ["HEYGEN_API_KEY"]}

    def _json(self, r: httpx.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        if r.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
            msg = body.get("error") if isinstance(body, dict) else body
            raise RuntimeError(f"HeyGen {r.status_code}: {str(msg)[:300]}")
        return body.get("data", body) if isinstance(body, dict) else body

    def upload(self, name: str, data: bytes, mime: str) -> str:
        d = self._json(self.c.post(f"{HEYGEN}/v3/assets", headers=self.h, files={"file": (name, data, mime)}))
        aid = d.get("asset_id") or d.get("id")
        if not aid:
            raise RuntimeError("HeyGen upload returned no asset id")
        return aid

    def voices(self) -> list[dict]:
        d = self._json(self.c.get(f"{HEYGEN}/v2/voices", headers=self.h))
        return d.get("voices", d) if isinstance(d, dict) else d

    def generate(self, payload: dict) -> str:
        return self._json(self.c.post(f"{HEYGEN}/v3/videos", headers=self.h, json=payload))["video_id"]

    def status(self, video_id: str) -> dict:
        return self._json(self.c.get(f"{HEYGEN}/v3/videos/{video_id}", headers=self.h))

    def remaining(self):
        return self._json(self.c.get(f"{HEYGEN}/v2/user/remaining_quota", headers=self.h))


_voices_cache: tuple[float, list] = (0.0, [])


def voices(hg: HeyGen) -> list[dict]:
    global _voices_cache
    if time.time() - _voices_cache[0] > 3600 or not _voices_cache[1]:
        _voices_cache = (time.time(), hg.voices())
    return _voices_cache[1]


# Who a look portrays. Sets the portrait (age, clothing) and nudges the voice choice.
ROLES = {
    "leader": ("school leader in their 40s, business-casual clothing", ""),
    "teacher": ("teacher in their 30s, smart-casual clothing", ""),
    "staff": ("school staff member in their 30s or 40s, smart-casual clothing", ""),
    "parent": ("parent in their late 30s, everyday casual clothing", ""),
    "student_elem": ("elementary school student about 9 years old, casual school clothes", "child"),
    "student_middle": ("middle school student about 12 years old, casual school clothes", "child"),
    "student_high": ("high school student about 16 years old, casual school clothes", "teen"),
    "student_college": ("college student about 20 years old, casual clothes", "young"),
}
YOUNG_WORDS = {"child": ("child", "kid", "boy", "girl", "young", "teen"), "teen": ("teen", "young", "youth"), "young": ("young",)}


def pick_voice(all_voices: list[dict], lang: str, gender: str, role: str = "") -> tuple[dict | None, str]:
    """Right language, then gender, then regional accent; multilingual voices as a last resort."""
    spec = VOICE_SPECS.get(lang) or VOICE_SPECS.get(lang.split("-")[0]) or ([lang], [])
    langs, accents = [l.lower() for l in spec[0]], spec[1]
    text = lambda v: " ".join(str(x) for x in v.values() if isinstance(x, (str, int))).lower()  # noqa: E731
    cands = [v for v in all_voices if any(l in str(v.get("language", "")).lower() for l in langs)]
    note = ""
    if not cands:
        cands = [v for v in all_voices if "multilingual" in str(v.get("language", "")).lower()]
        if not cands:
            return None, f"no {spec[0][0]} voice in the HeyGen account"
        note = f"no native {spec[0][0]} voice; using a multilingual one (check the accent)"

    def score(v):
        s = 10 if gender and str(v.get("gender", "")).lower() == gender.lower() else 0
        s += sum(5 for a in accents if a.lower() in text(v))
        young = ROLES.get(role, ("", ""))[1]
        if young:
            s += 8 if any(w in text(v) for w in YOUNG_WORDS[young]) else 0
        return s - (3 if "multilingual" in str(v.get("language", "")).lower() else 0)

    return max(cands, key=score), note


def cached_asset(hg: HeyGen, key: str, make) -> str:
    aid = store.hget(ASSET_HASH, key)
    if not aid:
        name, data, mime = make()
        aid = hg.upload(name, data, mime)
        store.hset(ASSET_HASH, key, aid)
    return aid


def script_hash(script: str) -> str:
    return hashlib.sha1(script.encode("utf-8")).hexdigest()[:12]


def start_video(*, script: str, persona_id: str, persona: dict, lang: str, title: str, base_url: str,
                hg: HeyGen | None = None) -> dict:
    """Start one HeyGen render. Returns the job entry stored on the sim."""
    hg = hg or HeyGen()
    voice, note = pick_voice(voices(hg), lang, persona.get("gender", ""), persona.get("role", ""))
    if not voice:
        raise RuntimeError(note)
    photo_path = (persona.get("photo") or {}).get("default", f"/assets/{persona_id}.png")
    bg = background_for(lang)
    payload = {"script": script, "voice_id": voice["voice_id"], "resolution": "720p",
               "aspect_ratio": "16:9", "output_format": "mp4", "title": title[:90]}
    if bg:
        payload["image"] = {"type": "asset_id", "asset_id": cached_asset(
            hg, f"photo:{photo_path}:framed90",
            lambda: ("portrait.jpg", frame_photo(site_file(photo_path, base_url)), "image/jpeg"))}
        payload["background"] = {"type": "image", "asset_id": cached_asset(
            hg, f"bg:{bg}", lambda: (Path(bg).name, site_file(bg, base_url), "image/jpeg"))}
        payload["remove_background"] = True
    else:
        payload["image"] = {"type": "asset_id", "asset_id": cached_asset(
            hg, f"photo:{photo_path}", lambda: ("portrait" + Path(photo_path.split("?")[0]).suffix, site_file(photo_path, base_url),
                                                "image/png" if photo_path.split("?")[0].endswith(".png") else "image/jpeg"))}
    payload["type"] = "image"
    job = hg.generate(payload)
    return {"status": "rendering", "job_id": job, "voice": voice.get("name") or voice.get("voice_id"),
            "voice_note": note, "script_hash": script_hash(script), "started_at": int(time.time())}


def finish_video(entry: dict, *, script: str, path_prefix: str, hg: HeyGen | None = None) -> dict:
    """Check a render; when done, copy the mp4 + captions to Blob. Returns the updated entry."""
    hg = hg or HeyGen()
    st = hg.status(entry["job_id"])
    s = st.get("status")
    if s == "failed":
        return {**entry, "status": "failed", "error": str(st.get("failure_message") or st.get("error") or "failed")[:300]}
    if s != "completed" or not st.get("video_url"):
        if time.time() - entry.get("started_at", time.time()) > 3 * 3600:
            return {**entry, "status": "failed", "error": "HeyGen took more than 3 hours"}
        return entry
    r = httpx.get(st["video_url"], timeout=120, follow_redirects=True)
    r.raise_for_status()
    mp4 = r.content
    duration = mp4_duration(mp4) or 10.0
    video_url = blob_put(f"{path_prefix}.mp4", mp4, "video/mp4")
    captions_url = blob_put(f"{path_prefix}.vtt", vtt(split_lines(script), duration).encode("utf-8"), "text/vtt")
    return {**entry, "status": "ready", "url": video_url, "captions": captions_url,
            "duration": round(duration, 1), "error": ""}


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------

def split_lines(script: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?。！？؟])\s+", script.strip()) if p.strip()]
    return parts or [script.strip()]


def mp4_duration(data: bytes) -> float | None:
    i = data.find(b"mvhd")
    if i < 0:
        return None
    if data[i + 4] == 1:
        timescale, duration = struct.unpack(">IQ", data[i + 24:i + 36])
    else:
        timescale, duration = struct.unpack(">II", data[i + 16:i + 24])
    return duration / timescale if timescale else None


def _ts(sec: float) -> str:
    h, rem = divmod(max(sec, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def vtt(lines: list[str], duration: float) -> str:
    lead, gap = 0.2, 0.15
    total = sum(len(l) for l in lines) or 1
    usable = max(duration - lead - gap * (len(lines) - 1), 0.5)
    t, out = lead, ["WEBVTT", ""]
    for n, line in enumerate(lines):
        end = duration if n == len(lines) - 1 else t + usable * len(line) / total
        out += [f"{_ts(t)} --> {_ts(end)}", line, ""]
        t = end + gap
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Storage for media: Vercel Blob
# ---------------------------------------------------------------------------

def blob_put(pathname: str, data: bytes, content_type: str) -> str:
    """Upload to Vercel Blob (public) and return its URL."""
    auth = blob_auth()
    if not auth:
        raise RuntimeError("Blob storage isn't connected (no read-write token or OIDC token + store id).")
    r = httpx.put(f"{BLOB}/{pathname}", content=data, timeout=120, headers={
        **auth,
        "x-vercel-blob-access": "public",
        "x-api-version": "7",
        "x-content-type": content_type,
        "x-add-random-suffix": "1",
        "access": "public",
        "x-cache-control-max-age": "31536000",
    })
    if r.status_code >= 400:
        hint = " (the Blob store may be private: create a Public Blob store and connect it to this project)" if "private" in r.text.lower() else ""
        raise RuntimeError(f"Blob upload {r.status_code}: {r.text[:200]}{hint}")
    return r.json()["url"]


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

IMAGE_STYLE = ("Photorealistic, natural light, softly composed editorial photo of a school or office setting. "
               "No text, no signage, no logos, no recognizable real people. ")


def _image_models() -> list[str]:
    """OpenAI renames image models; try the configured one, then current and older names."""
    first = os.getenv("OPENAI_IMAGE_MODEL", "").strip()
    return list(dict.fromkeys([m for m in (first, "gpt-image-2", "gpt-image-1.5", "gpt-image-1") if m]))


def openai_image(prompt: str, size: str, quality: str = "") -> bytes:
    """Generate one image and return its bytes. Falls back to the next model name when
    one doesn't exist, and to the bare minimum of parameters if one is rejected."""
    quality = quality or os.getenv("OPENAI_IMAGE_QUALITY", "medium")
    last = ""
    for model in _image_models():
        for body in ({"model": model, "prompt": prompt, "size": size, "quality": quality,
                      "output_format": "jpeg", "output_compression": 85, "n": 1},
                     {"model": model, "prompt": prompt, "size": size}):
            r = httpx.post("https://api.openai.com/v1/images/generations", timeout=240,
                           headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}, json=body)
            if r.status_code < 400:
                item = r.json()["data"][0]
                if item.get("b64_json"):
                    return base64.b64decode(item["b64_json"])
                img = httpx.get(item["url"], timeout=120, follow_redirects=True)
                img.raise_for_status()
                return img.content
            last = f"Image API {r.status_code}: {r.text[:300]}"
            try:
                err = r.json().get("error", {})
            except ValueError:
                err = {}
            if err.get("param") == "model" or "does not exist" in str(err.get("message", "")):
                break  # try the next model name
            if r.status_code == 400 and len(body) > 3:
                continue  # retry with only model/prompt/size
            raise RuntimeError(last)  # auth, verification, billing...: stop here
    raise RuntimeError(last or "No image model available.")


def make_image(prompt: str, sim_id: str, node_id: str) -> str:
    """Create one scene illustration and return a URL for it."""
    data = openai_image(IMAGE_STYLE + prompt, "1536x1024")
    if blob_ready():
        return blob_put(f"sims/{sim_id}/images/{node_id}.jpg", data, "image/jpeg")
    import sims  # fall back to the runtime store
    return sims.save_image(sim_id, node_id, base64.b64encode(data).decode())


PORTRAIT_STYLE = ("Photorealistic head-and-shoulders portrait for an educational video, looking at the camera, "
                  "friendly expression, soft even light, plain light-grey studio background, centred, "
                  "no text, no logos, not a real or famous person. ")


def make_portrait(prompt: str, role: str = "") -> bytes:
    """A character portrait (image bytes) from a description."""
    who = f"The person is a {ROLES[role][0]}. " if role in ROLES else ""
    return openai_image(PORTRAIT_STYLE + who + prompt, "1024x1536")
