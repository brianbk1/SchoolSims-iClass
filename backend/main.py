from pathlib import Path
from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Any
import json, copy, os, sys, tempfile
from dotenv import load_dotenv
from tts import get_audio, tts_enabled
import localize

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")  # backend/.env
load_dotenv(BASE_DIR.parent / ".env.local", override=False)  # project root .env.local (ElevenLabs key)
DIST_DIR = BASE_DIR.parent / "dist"
ON_VERCEL = bool(os.getenv("VERCEL"))

app = FastAPI(title="SchoolSims Player")

with open(BASE_DIR / "scenario.json") as f:
    SCENARIO: dict = json.load(f)



def valid_langs() -> set[str]:
    """Approved languages = language blocks in scenario.json (grows when a draft is approved)."""
    return {k for k, v in SCENARIO.items() if isinstance(v, dict) and not k.startswith("_") and "nodes" in v}


# Characters (look, names per language, photo, videos) live in personas.json.
with open(BASE_DIR / "personas.json") as f:
    PERSONAS: dict = json.load(f)["personas"]

VALID_ETHNICITIES = set(PERSONAS)
# The scenario text is written with this placeholder name; it is swapped for the
# chosen character's name (in the chosen language) everywhere it appears.
PLACEHOLDER_NAME = "Jordan Chen"

AVAILABLE_VIDEOS = {(pid, lang) for pid, p in PERSONAS.items() for lang in p.get("video", {})}


def lang_chain(code: str) -> list[str]:
    """Most specific first: 'es-AR' -> ['es-AR', 'es', 'en']."""
    chain = [code, code.split("-")[0], "en"]
    return list(dict.fromkeys(chain))


def pick(by_lang: dict, code: str, default=None):
    """Value for the most specific language available (regional -> base -> English)."""
    for c in lang_chain(code):
        if by_lang.get(c):
            return by_lang[c]
    return default


def playable(pid: str, lang: str) -> bool:
    """A character can be used in a language if it has a video in that language,
    its base language (es-AR -> es), or English to fall back on."""
    return pick(PERSONAS[pid].get("video", {}), lang) is not None


def language_label(code: str, display_lang: str) -> str:
    return (SCENARIO[display_lang]["ui"].get("language_labels", {}).get(code)
            or SCENARIO[code]["ui"].get("language_labels", {}).get(code)
            or code)


def suggested_persona(lang: str) -> str | None:
    # An exact match wins (sw-TZ), then the base language (es-MX -> es).
    for c in lang_chain(lang)[:-1] if lang != "en" else ["en"]:
        for pid, p in PERSONAS.items():
            if c in p.get("suggested_for_langs", []) and playable(pid, lang):
                return pid
    return None


def persona_name(pid: str, lang: str) -> str:
    return pick(PERSONAS[pid].get("name", {}), lang, PLACEHOLDER_NAME)


def swap_name(value: Any, name: str) -> Any:
    if isinstance(value, str):
        return value.replace(PLACEHOLDER_NAME, name)
    if isinstance(value, list):
        return [swap_name(v, name) for v in value]
    if isinstance(value, dict):
        return {k: swap_name(v, name) for k, v in value.items()}
    return value


@app.get("/api/options")
def get_options(lang: str = Query("en")):
    langs = valid_langs()
    display_lang = lang if lang in langs else "en"
    ui = SCENARIO[display_lang]["ui"]
    return {
        "languages": [
            {"value": code, "label": language_label(code, display_lang)}
            for code in sorted(langs)
        ],
        "ethnicities": [
            {"value": code, "label": ui["ethnicity_labels"].get(code, code)}
            for code in sorted(VALID_ETHNICITIES)
        ],
        "available_combos": sorted([pid, code] for pid in PERSONAS for code in langs if playable(pid, code)),
        "suggested": {code: suggested_persona(code) for code in sorted(langs)},
        "setup_ui": {
            "sim_title": ui["sim_title"],
            "setup_subtitle": ui["setup_subtitle"],
            "before_you_begin_title": ui["before_you_begin_title"],
            "before_you_begin_body": ui["before_you_begin_body"],
            "lang_label": ui["lang_label"],
            "eth_label": ui["eth_label"],
            "start_btn": ui["start_btn"],
            "setup_badge": ui.get("setup_badge", "Interactive Simulation"),
            "suggested_hint": ui.get("suggested_hint", ""),
        },
    }


@app.get("/api/simulation")
def get_simulation(
    lang: str = Query(..., description="en or es"),
    ethnicity: str = Query(..., description="persona id from personas.json"),
):
    if lang not in valid_langs():
        raise HTTPException(400, detail=f"lang must be one of {sorted(valid_langs())}")
    if ethnicity not in VALID_ETHNICITIES:
        raise HTTPException(400, detail=f"ethnicity must be one of {sorted(VALID_ETHNICITIES)}")

    persona = PERSONAS[ethnicity]
    name = persona_name(ethnicity, lang)
    nodes = swap_name(copy.deepcopy(SCENARIO[lang]["nodes"]), name)
    for node in nodes.values():
        if "character_name" in node:
            node["character_name"] = name
    photos = persona.get("photo", {})
    videos = persona.get("video", {})
    video = pick(videos, lang, "")

    return {
        "start_node_id": SCENARIO["start_node_id"],
        "nodes": nodes,
        "ui": swap_name(SCENARIO[lang]["ui"], name),
        "media": {
            "photo": pick(photos, lang) or photos.get("default", ""),
            "video_template": video,
            "photo_alt": nodes.get("text_image", {}).get("character_alt", name),
            "character_name": name,
            # True when the video is in this language or its base language (not English fallback).
            "has_video": any(videos.get(c) for c in lang_chain(lang)[:2]),
            "base_lang": lang.split("-")[0],
        },
    }


class ScenarioUpdate(BaseModel):
    start_node_id: str
    nodes: dict[str, Any]
    ui: dict[str, Any]


def narration_text(lang: str, node_id: str) -> str:
    """Build a slide's narration server-side (same rules the frontend uses), so the
    public audio endpoint can only ever voice scenario text, not arbitrary input."""
    node = SCENARIO.get(lang, {}).get("nodes", {}).get(node_id)
    if not node:
        return ""
    if node_id == "context":
        return " ".join(p for p in [node.get("body"), node.get("learning_objective_body")] if p)
    if node_id == "decision":
        parts = [node.get("prompt", "")] + [f"Option {c['id']}: {c['label']}" for c in node.get("choices", [])]
        return ". ".join(parts)
    return node.get("body", "") or ""


@app.post("/api/tts/preload")
def tts_preload(body: dict):
    """Return audio URLs for slides that have narration. Audio is generated on first
    play by /api/tts/audio and then cached by the CDN (and the local file cache)."""
    texts: dict = body.get("texts", {})
    lang: str = body.get("lang", "en")
    if lang not in valid_langs() or not tts_enabled(lang):
        return {}
    return {
        node_id: f"/api/tts/audio?lang={lang}&node={node_id}"
        for node_id, text in texts.items()
        if text and narration_text(lang, node_id)
    }


@app.get("/api/tts/audio")
def tts_audio(lang: str = Query(...), node: str = Query(...)):
    if lang not in valid_langs():
        raise HTTPException(400, detail="invalid lang")
    text = narration_text(lang, node)
    if not text:
        raise HTTPException(404, detail="no narration for that slide")
    try:
        data = get_audio(text, lang=lang)
    except Exception as e:  # ElevenLabs error, bad key, quota, etc.
        print(f"TTS error: {e}", file=sys.stderr)
        raise HTTPException(502, detail="TTS unavailable")
    if not data:
        raise HTTPException(404, detail="TTS disabled")
    return Response(
        content=data,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=86400, s-maxage=31536000"},
    )


@app.post("/api/scenario/{lang}")
def save_scenario(lang: str, body: ScenarioUpdate):
    if lang not in valid_langs():
        raise HTTPException(400, detail=f"lang must be one of {sorted(valid_langs())}")
    if body.start_node_id not in body.nodes:
        raise HTTPException(400, detail="start_node_id not found in nodes")
    global SCENARIO
    updated = copy.deepcopy(SCENARIO)
    updated[lang]["nodes"] = body.nodes
    updated[lang]["ui"] = body.ui
    updated["start_node_id"] = body.start_node_id
    try:
        with open(BASE_DIR / "scenario.json", "w") as f:
            json.dump(updated, f, indent=2, ensure_ascii=False)
    except OSError:
        raise HTTPException(
            503,
            detail="Scenario editing isn't available on the hosted site. Edit backend/scenario.json and redeploy.",
        )
    SCENARIO = updated
    return {"ok": True}


def reviews_file() -> Path:
    # On Vercel the disk is temporary, so reviews there are only reliable in the logs.
    return (Path(tempfile.gettempdir()) if ON_VERCEL else BASE_DIR) / "reviews.json"


class ReviewPayload(BaseModel):
    rating: int
    experience: str
    improvements: str
    lang: str
    ethnicity: str
    chosen_option: str
    # Optional check on the localization: "yes" | "partly" | "no" | "" (skipped)
    regional_fit: str = ""
    regional_fit_details: str = ""


@app.post("/api/review")
def submit_review(body: ReviewPayload):
    if body.rating < 1 or body.rating > 5:
        raise HTTPException(400, detail="rating must be 1-5")
    if body.regional_fit not in ("", "yes", "partly", "no"):
        raise HTTPException(400, detail="regional_fit must be yes, partly, no or empty")
    import datetime
    entry = body.model_dump()
    entry["timestamp"] = datetime.datetime.utcnow().isoformat()
    # Always log the review so it shows up in Vercel's function logs.
    print("REVIEW " + json.dumps(entry, ensure_ascii=False), flush=True)
    # On Vercel the disk is temporary, so the file copy is not permanent there.
    path = reviews_file()
    existing = []
    if path.exists():
        existing = json.loads(path.read_text())
    existing.append(entry)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin: AI-drafted, human-approved locales (see backend/localize.py)
# ---------------------------------------------------------------------------
from fastapi import Header, Depends  # noqa: E402


def require_admin(x_admin_token: str | None = Header(default=None)):
    expected = os.getenv("ADMIN_TOKEN")
    if expected:
        if x_admin_token != expected:
            raise HTTPException(401, detail="Admin token required.")
    elif ON_VERCEL:
        raise HTTPException(403, detail="Set ADMIN_TOKEN to use the admin page on a hosted site.")


def authoring_writable() -> bool:
    return not ON_VERCEL and os.access(BASE_DIR, os.W_OK)


def write_scenario(updated: dict) -> None:
    global SCENARIO
    with open(BASE_DIR / "scenario.json", "w") as f:
        json.dump(updated, f, indent=2, ensure_ascii=False)
        f.write("\n")
    SCENARIO = updated


class DraftRequest(BaseModel):
    code: str
    setting: str
    language_name: str = ""
    notes: str = ""


class DraftEdits(BaseModel):
    edits: dict[str, str]


@app.get("/api/admin/locales", dependencies=[Depends(require_admin)])
def admin_locales():
    meta = SCENARIO.get("_locale_meta", {})
    return {
        "approved": [
            {"code": c, "label": language_label(c, "en"), "native_label": language_label(c, c),
             "setting": meta.get(c, {}).get("setting", ""), "approved_at": meta.get(c, {}).get("approved_at", "")}
            for c in sorted(valid_langs())
        ],
        "drafts": localize.list_drafts(),
        "writable": authoring_writable(),
        "generator_ready": localize.generator_ready(),
    }


@app.get("/api/admin/feedback", dependencies=[Depends(require_admin)])
def admin_feedback():
    """Learners' answers to "did the character and setting feel accurate for your region?",
    grouped by language, with the comments for 'partly' / 'no'."""
    path = reviews_file()
    reviews = json.loads(path.read_text()) if path.exists() else []
    out: dict[str, dict] = {}
    for r in reviews:
        fit = r.get("regional_fit") or ""
        if not fit:
            continue
        d = out.setdefault(r.get("lang", "?"), {"yes": 0, "partly": 0, "no": 0, "comments": []})
        d[fit] = d.get(fit, 0) + 1
        if fit != "yes" and (r.get("regional_fit_details") or "").strip():
            d["comments"].append({
                "fit": fit,
                "character": r.get("ethnicity", ""),
                "text": r["regional_fit_details"].strip(),
                "timestamp": r.get("timestamp", ""),
            })
    for d in out.values():
        d["comments"].sort(key=lambda c: c["timestamp"], reverse=True)
    return {"by_lang": out, "persistent": not ON_VERCEL}


@app.post("/api/admin/drafts", dependencies=[Depends(require_admin)])
def admin_create_draft(body: DraftRequest):
    if not authoring_writable():
        raise HTTPException(503, detail="Drafting works in your local copy or a Codespace, not on the hosted site.")
    if not localize.generator_ready():
        raise HTTPException(503, detail="Set ANTHROPIC_API_KEY to generate drafts.")
    if not body.setting.strip():
        raise HTTPException(400, detail="Describe the setting, e.g. 'Kathmandu, Nepal — private college'.")
    try:
        return localize.generate_draft(SCENARIO, body.code, body.language_name.strip(), body.setting.strip(), body.notes.strip())
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:  # API error, bad key, etc.
        print(f"Draft generation failed: {e}", file=sys.stderr)
        raise HTTPException(502, detail=f"Generation failed: {e}")


def _draft_or_404(code: str) -> dict:
    try:
        d = localize.load_draft(code)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    if not d:
        raise HTTPException(404, detail="No draft for that language.")
    return d


@app.get("/api/admin/drafts/{code}", dependencies=[Depends(require_admin)])
def admin_get_draft(code: str):
    d = _draft_or_404(code)
    en = localize.flatten(localize.english_block(SCENARIO))
    src = localize.source_lang(SCENARIO, code)
    cur = (localize.flatten({"nodes": SCENARIO[src]["nodes"], "ui": SCENARIO[src]["ui"]})
           if src else {})
    flat = localize.flatten(d["block"])
    d["rows"] = [
        {"path": path, "english": en.get(path, ""), "current": cur.get(path, ""),
         "text": flat.get(path, ""), "back_translation": d.get("back_translation", {}).get(path, "")}
        for path in d["fields"]
    ]
    return d


@app.put("/api/admin/drafts/{code}", dependencies=[Depends(require_admin)])
def admin_edit_draft(code: str, body: DraftEdits):
    _draft_or_404(code)
    if not authoring_writable():
        raise HTTPException(503, detail="Editing drafts works in your local copy or a Codespace.")
    localize.update_draft(SCENARIO, code, body.edits)
    return admin_get_draft(code)


@app.post("/api/admin/drafts/{code}/approve", dependencies=[Depends(require_admin)])
def admin_approve_draft(code: str):
    d = _draft_or_404(code)
    if not authoring_writable():
        raise HTTPException(503, detail="Approving works in your local copy or a Codespace; then commit and redeploy.")
    problems = localize.validate(SCENARIO, code, d["block"]) + [f"Model skipped: {p}" for p in d.get("skipped", [])]
    if problems:
        raise HTTPException(400, detail="Fix these first: " + "; ".join(problems[:5]))
    updated = copy.deepcopy(SCENARIO)
    updated[code] = d["block"]
    meta = updated.setdefault("_locale_meta", {})
    meta[code] = {
        "setting": d.get("setting", ""),
        "approved_at": datetime_now(),
        "source": f"generated by {d.get('model', 'AI')}, reviewed in admin",
    }
    write_scenario(updated)
    localize.delete_draft(code)
    return {"ok": True, "code": code}


@app.delete("/api/admin/drafts/{code}", dependencies=[Depends(require_admin)])
def admin_discard_draft(code: str):
    _draft_or_404(code)
    if not authoring_writable():
        raise HTTPException(503, detail="Not available on the hosted site.")
    localize.delete_draft(code)
    return {"ok": True}


def datetime_now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    @app.get("/")
    def serve_index():
        return FileResponse(str(DIST_DIR / "index.html"))

    @app.get("/admin")
    def serve_admin():
        return FileResponse(str(DIST_DIR / "admin.html"))

    @app.get("/{full_path:path}")
    def catch_all(full_path: str):
        f = (DIST_DIR / full_path).resolve()
        if full_path and f.is_file() and DIST_DIR.resolve() in f.parents:
            return FileResponse(str(f))
        return FileResponse(str(DIST_DIR / "index.html"))
