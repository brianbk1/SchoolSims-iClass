#!/usr/bin/env python3
"""Generate the localized character videos with HeyGen and drop them into the app.

Run from the repo root (needs HEYGEN_API_KEY in .env.local or the environment):

    python scripts/make_videos.py --check            # is the key working? how many credits?
    python scripts/make_videos.py --list-voices Spanish
    python scripts/make_videos.py --dry-run          # what would be made (no cost)
    python scripts/make_videos.py --only es-AR       # make one clip first and watch it
    python scripts/make_videos.py                    # make everything still missing

For each clip in scripts/heygen/clips.json it:
  1. uploads the character's photo (once) and the region's background image (if present),
  2. asks HeyGen (v3 API, POST /v3/videos with type "image") for a 720p 16:9 video of the
     photo speaking the lines in the chosen voice, with the photo's own background removed
     and the regional one placed behind,
  3. waits for it, downloads it to public/assets/<character>_<locale>.mp4,
  4. writes matching subtitles (public/assets/captions_<character>_<locale>.vtt),
  5. records both in backend/personas.json so the app plays them for that language.

Clips that already exist are skipped (use --force to remake). Nothing is spent in
--check, --list-voices or --dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
CLIPS_FILE = REPO / "scripts/heygen/clips.json"
CACHE_FILE = REPO / "scripts/heygen/.upload-cache.json"   # uploaded photo/background ids (git-ignored)
PERSONAS_FILE = REPO / "backend/personas.json"
ASSETS = REPO / "public/assets"

API = "https://api.heygen.com"


# --------------------------------------------------------------------------- setup

def load_key() -> str:
    env = REPO / ".env.local"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("HEYGEN_API_KEY="):
                os.environ.setdefault("HEYGEN_API_KEY", line.split("=", 1)[1].strip().strip('"').strip("'"))
    key = os.environ.get("HEYGEN_API_KEY", "")
    if not key:
        sys.exit("HEYGEN_API_KEY is not set. Add it to .env.local at the repo root.")
    return key


class HeyGen:
    def __init__(self, key: str, client: httpx.Client | None = None):
        self.c = client or httpx.Client(timeout=120)
        self.h = {"X-Api-Key": key}

    def _json(self, r: httpx.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        if r.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
            raise RuntimeError(f"HeyGen {r.request.method} {r.request.url.path} -> {r.status_code}: {json.dumps(body)[:400]}")
        return body.get("data", body) if isinstance(body, dict) else body

    def remaining_quota(self):
        return self._json(self.c.get(f"{API}/v2/user/remaining_quota", headers=self.h))

    def voices(self) -> list[dict]:
        d = self._json(self.c.get(f"{API}/v2/voices", headers=self.h))
        return d.get("voices", d) if isinstance(d, dict) else d

    def upload_asset(self, path: Path) -> str:
        """POST /v3/assets (multipart, field 'file') -> asset id."""
        r = self.c.post(f"{API}/v3/assets", headers=self.h,
                        files={"file": (path.name, path.read_bytes(), mime(path))})
        d = self._json(r)
        aid = d.get("asset_id") or d.get("id")
        if not aid:
            raise RuntimeError(f"HeyGen upload returned no asset id: {json.dumps(d)[:300]}")
        return aid

    def generate(self, payload: dict) -> str:
        """POST /v3/videos -> video id."""
        return self._json(self.c.post(f"{API}/v3/videos", headers=self.h, json=payload))["video_id"]

    def status(self, video_id: str) -> dict:
        """GET /v3/videos/{id} -> {status: pending|processing|completed|failed, video_url, failure_message}."""
        return self._json(self.c.get(f"{API}/v3/videos/{video_id}", headers=self.h))

    def download(self, url: str, dest: Path) -> None:
        with self.c.stream("GET", url) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)


FRAMED_DIR = REPO / "scripts/heygen/.framed"   # widened copies of the photos (git-ignored)


def frame_photo(photo: Path, person_height: float) -> Path:
    """Place a portrait photo on a 16:9 canvas so the person keeps a natural
    video-call size. Without this HeyGen crops a tall photo to fill the wide
    frame, which zooms in on the face. The canvas around the photo is removed
    together with the photo's own background (remove_background)."""
    try:
        from PIL import Image
    except ImportError:
        sys.exit("Framing photos needs Pillow: run  pip install pillow  and try again "
                 "(or set \"frame_photo\": false in scripts/heygen/clips.json).")
    W, H = 1920, 1080
    im = Image.open(photo).convert("RGB")
    scale = (H * person_height) / im.height
    im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    if im.width > W:                       # very wide photo: fit by width instead
        im = im.resize((W, round(im.height * W / im.width)), Image.LANCZOS)
    # Fill with the photo's own edge colour so background removal sees one continuous backdrop.
    edge = im.resize((1, 1)).getpixel((0, 0))
    canvas = Image.new("RGB", (W, H), edge)
    canvas.paste(im, ((W - im.width) // 2, H - im.height))   # centred, sitting on the bottom edge
    FRAMED_DIR.mkdir(parents=True, exist_ok=True)
    out = FRAMED_DIR / f"{photo.stem}-{int(person_height * 100)}.jpg"
    canvas.save(out, quality=92)
    return out


def mime(p: Path) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[p.suffix.lower()]


# --------------------------------------------------------------------------- voices

def voice_text(v: dict) -> str:
    return " ".join(str(x) for x in v.values() if isinstance(x, (str, int))).lower()


def pick_voice(voices: list[dict], spec: dict) -> tuple[dict | None, str]:
    """Best voice for a clip: right language, right gender, region accent if available."""
    if spec.get("voice_id"):
        v = next((v for v in voices if v.get("voice_id") == spec["voice_id"]), None)
        return v, "set in clips.json" if v else "voice_id from clips.json not found"
    langs = [l.lower() for l in spec["language"]]
    gender = (spec.get("gender") or "").lower()
    cands = [v for v in voices if any(l in str(v.get("language", "")).lower() for l in langs)]
    fallback = ""
    if not cands:
        # HeyGen's multilingual voices can speak most languages, but the accent may not be native.
        cands = [v for v in voices if "multilingual" in str(v.get("language", "")).lower()]
        if not cands:
            return None, f"no {'/'.join(spec['language'])} or multilingual voice in your HeyGen account"
        fallback = f"no native {spec['language'][0]} voice; using a multilingual voice (check the accent)"
    def score(v):
        t = voice_text(v)
        s = 0
        if gender and str(v.get("gender", "")).lower() == gender:
            s += 10
        s += sum(5 for a in spec.get("accent", []) if a.lower() in t)
        if "multilingual" in str(v.get("language", "")).lower():
            s -= 3          # prefer native voices over multilingual ones
        return s
    best = max(cands, key=score)
    accent_hit = any(a.lower() in voice_text(best) for a in spec.get("accent", []))
    note = fallback or ("" if accent_hit or not spec.get("accent") else f"no {spec['accent'][0]} accent found; using closest")
    return best, note


# --------------------------------------------------------------------------- subtitles

def mp4_duration(path: Path) -> float | None:
    """Duration from the mp4 'mvhd' box (no ffmpeg needed)."""
    data = path.read_bytes()
    i = data.find(b"mvhd")
    if i < 0:
        return None
    version = data[i + 4]
    if version == 1:
        timescale, duration = struct.unpack(">IQ", data[i + 24:i + 36])
    else:
        timescale, duration = struct.unpack(">II", data[i + 16:i + 24])
    return duration / timescale if timescale else None


def write_vtt(lines: list[str], duration: float, dest: Path) -> None:
    """Spread the lines over the clip in proportion to their length."""
    lead, gap = 0.2, 0.15
    total_chars = sum(len(l) for l in lines) or 1
    usable = max(duration - lead - gap * (len(lines) - 1), 0.5)
    t, out = lead, ["WEBVTT", ""]
    for n, line in enumerate(lines):
        span = usable * len(line) / total_chars
        end = duration if n == len(lines) - 1 else t + span
        out += [f"{ts(t)} --> {ts(end)}", line, ""]
        t = end + gap
    dest.write_text("\n".join(out), encoding="utf-8")


def ts(sec: float) -> str:
    h, rem = divmod(max(sec, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


# --------------------------------------------------------------------------- main

def main(argv=None, client: httpx.Client | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify the API key and show remaining credits")
    ap.add_argument("--list-voices", metavar="LANGUAGE", help="list HeyGen voices for a language, e.g. Spanish")
    ap.add_argument("--dry-run", action="store_true", help="show what would be made; spends nothing")
    ap.add_argument("--only", nargs="+", metavar="LOCALE", help="only these locales, e.g. --only es-AR he")
    ap.add_argument("--force", action="store_true", help="remake clips that already exist")
    ap.add_argument("--poll-seconds", type=float, default=10)
    args = ap.parse_args(argv)

    hg = HeyGen(load_key(), client)

    if args.check:
        print("API key works. Remaining quota:", json.dumps(hg.remaining_quota()))
        print(f"{len(hg.voices())} voices available.")
        return 0

    if args.list_voices:
        want = args.list_voices.lower()
        rows = [v for v in hg.voices() if want in str(v.get("language", "")).lower()]
        for v in sorted(rows, key=lambda v: (str(v.get("gender")), str(v.get("name")))):
            print(f"{v.get('voice_id')}  {v.get('gender', ''):7} {v.get('language', ''):22} {v.get('name', '')}")
        print(f"{len(rows)} voice(s). Put a voice_id in scripts/heygen/clips.json to force one.")
        return 0

    cfg = json.loads(CLIPS_FILE.read_text())
    clips = [c for c in cfg["clips"] if not args.only or c["locale"] in args.only]
    if args.only and len(clips) != len(args.only):
        known = {c["locale"] for c in cfg["clips"]}
        sys.exit(f"Unknown locale(s): {sorted(set(args.only) - known)}. Known: {sorted(known)}")
    settings = cfg.get("settings", {})
    voices = hg.voices()

    plan, problems = [], []
    for c in clips:
        out = ASSETS / f"{c['character']}_{c['locale']}.mp4"
        voice, note = pick_voice(voices, c["voice"])
        char = cfg["characters"][c["character"]]
        photo = REPO / char["photo"]
        bg = REPO / c["background"] if c.get("background") else None
        status = "exists, skip" if out.exists() and not args.force else "make"
        if not voice:
            problems.append(f"{c['locale']}: {note}")
            status = "NO VOICE"
        if not char.get("heygen_avatar_id") and not photo.exists():
            problems.append(f"{c['locale']}: photo not found: {char['photo']}")
            status = "NO PHOTO"
        plan.append((c, out, voice, note, photo, bg, status))

    words = 0
    print(f"\n{'locale':7} {'character':18} {'voice':38} {'background':12} status")
    print("-" * 92)
    for c, out, voice, note, photo, bg, status in plan:
        vname = f"{voice.get('name', '?')} ({voice.get('language', '')})" if voice else "—"
        bgs = "yes" if bg and bg.exists() else "photo's own"
        print(f"{c['locale']:7} {c['character_name'][:18]:18} {vname[:38]:38} {bgs:12} {status}")
        if note:
            print(f"{'':7} note: {note}")
        if status == "make":
            words += sum(len(l.split()) for l in c["lines"])
    to_make = [p for p in plan if p[6] == "make"]
    print("-" * 92)
    print(f"{len(to_make)} clip(s) to make, about {words / 2.5:.0f} seconds of speech in total "
          f"(roughly {len(to_make) * 10} seconds of video).")
    if problems:
        print("\nFix before generating:\n  " + "\n  ".join(problems))
    if args.dry_run or not to_make:
        return 0 if not problems else 1
    if problems:
        print("\nOnly clips without problems will be made.")

    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    personas_doc = json.loads(PERSONAS_FILE.read_text())
    made = []
    for c, out, voice, note, photo, bg, status in to_make:
        loc, pid = c["locale"], c["character"]
        print(f"\n[{loc}] {c['character_name']}: ", end="", flush=True)
        try:
            char = cfg["characters"][pid]
            payload = {
                "script": " ".join(c["lines"]),
                "voice_id": voice["voice_id"],
                "resolution": settings.get("resolution", "720p"),
                "aspect_ratio": "16:9",
                "output_format": "mp4",
                "title": f"iClass {pid} {loc}",
            }
            if char.get("heygen_avatar_id"):
                payload.update({"type": "avatar", "avatar_id": char["heygen_avatar_id"]})
            else:
                framing = bool(settings.get("frame_photo", True)) and bool(bg and bg.exists()) \
                    and bool(settings.get("remove_photo_background", True))
                ph = float(settings.get("person_height", 0.9))
                key = f"v3photo:{char['photo']}" + (f":framed{int(ph * 100)}" if framing else "")
                if key not in cache:
                    print("uploading photo… ", end="", flush=True)
                    cache[key] = hg.upload_asset(frame_photo(photo, ph) if framing else photo)
                    CACHE_FILE.write_text(json.dumps(cache, indent=2))
                payload.update({"type": "image", "image": {"type": "asset_id", "asset_id": cache[key]}})
            if bg and bg.exists():
                key = f"v3bg:{c['background']}"
                if key not in cache:
                    print("uploading background… ", end="", flush=True)
                    cache[key] = hg.upload_asset(bg)
                    CACHE_FILE.write_text(json.dumps(cache, indent=2))
                payload["background"] = {"type": "image", "asset_id": cache[key]}
                payload["remove_background"] = bool(settings.get("remove_photo_background", True))
            vid = hg.generate(payload)
            print(f"generating ({vid})", end="", flush=True)
            while True:
                time.sleep(args.poll_seconds)
                st = hg.status(vid)
                s = st.get("status")
                if s == "completed":
                    break
                if s == "failed":
                    why = st.get("failure_message") or st.get("error")
                    raise RuntimeError(f"HeyGen could not make this video: {json.dumps(why)}")
                print(".", end="", flush=True)
            hg.download(st["video_url"], out)
            duration = mp4_duration(out) or 10.0
            vtt = ASSETS / f"captions_{pid}_{loc}.vtt"
            write_vtt(c["lines"], duration, vtt)
            p = personas_doc["personas"][pid]
            p.setdefault("video", {})[loc] = f"/assets/{out.name}"
            p.setdefault("captions", {})[loc] = f"/assets/{vtt.name}"
            PERSONAS_FILE.write_text(json.dumps(personas_doc, indent=2, ensure_ascii=False) + "\n")
            print(f" done, {duration:.1f}s -> {out.relative_to(REPO)}")
            made.append(loc)
        except Exception as e:  # keep going with the other clips
            print(f"\n  FAILED: {e}")
            problems.append(f"{loc}: {e}")

    print(f"\nMade {len(made)} of {len(to_make)}: {', '.join(made) or 'none'}")
    if made:
        print("Restart the server and try those languages. Then commit public/assets, backend/personas.json and push.")
    if problems:
        print("Problems:\n  " + "\n  ".join(problems))
    return 0 if len(made) == len(to_make) else 1


if __name__ == "__main__":
    sys.exit(main())
