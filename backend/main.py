from pathlib import Path
from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Any
import json, copy, os, sys, tempfile
from dotenv import load_dotenv
from tts import get_audio, tts_enabled

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")  # backend/.env
load_dotenv(BASE_DIR.parent / ".env.local", override=False)  # project root .env.local (ElevenLabs key)
DIST_DIR = BASE_DIR.parent / "dist"
ON_VERCEL = bool(os.getenv("VERCEL"))

app = FastAPI(title="SchoolSims Player")

with open(BASE_DIR / "scenario.json") as f:
    SCENARIO: dict = json.load(f)

VALID_LANGS = {"en", "es", "ar", "zh", "ne", "sw", "ur"}
# Characters (look, names per language, photo, videos) live in personas.json.
with open(BASE_DIR / "personas.json") as f:
    PERSONAS: dict = json.load(f)["personas"]

VALID_ETHNICITIES = set(PERSONAS)
# The scenario text is written with this placeholder name; it is swapped for the
# chosen character's name (in the chosen language) everywhere it appears.
PLACEHOLDER_NAME = "Jordan Chen"

AVAILABLE_VIDEOS = {(pid, lang) for pid, p in PERSONAS.items() for lang in p.get("video", {})}


def suggested_persona(lang: str) -> str | None:
    for pid, p in PERSONAS.items():
        if lang in p.get("suggested_for_langs", []) and (pid, lang) in AVAILABLE_VIDEOS:
            return pid
    return None


def persona_name(pid: str, lang: str) -> str:
    names = PERSONAS[pid].get("name", {})
    return names.get(lang) or names.get("en") or PLACEHOLDER_NAME


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
    display_lang = lang if lang in VALID_LANGS else "en"
    ui = SCENARIO[display_lang]["ui"]
    return {
        "languages": [
            {"value": code, "label": ui["language_labels"][code]}
            for code in sorted(VALID_LANGS)
        ],
        "ethnicities": [
            {"value": code, "label": ui["ethnicity_labels"][code]}
            for code in sorted(VALID_ETHNICITIES)
        ],
        "available_combos": [list(c) for c in sorted(AVAILABLE_VIDEOS)],
        "suggested": {code: suggested_persona(code) for code in sorted(VALID_LANGS)},
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
    if lang not in VALID_LANGS:
        raise HTTPException(400, detail=f"lang must be one of {sorted(VALID_LANGS)}")
    if ethnicity not in VALID_ETHNICITIES:
        raise HTTPException(400, detail=f"ethnicity must be one of {sorted(VALID_ETHNICITIES)}")

    persona = PERSONAS[ethnicity]
    name = persona_name(ethnicity, lang)
    nodes = swap_name(copy.deepcopy(SCENARIO[lang]["nodes"]), name)
    for node in nodes.values():
        if "character_name" in node:
            node["character_name"] = name
    photos = persona.get("photo", {})
    video = persona.get("video", {}).get(lang) or persona.get("video", {}).get("en", "")

    return {
        "start_node_id": SCENARIO["start_node_id"],
        "nodes": nodes,
        "ui": swap_name(SCENARIO[lang]["ui"], name),
        "media": {
            "photo": photos.get(lang) or photos.get("default", ""),
            "video_template": video,
            "photo_alt": nodes.get("text_image", {}).get("character_alt", name),
            "character_name": name,
            "has_video": (ethnicity, lang) in AVAILABLE_VIDEOS,
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
    if lang not in VALID_LANGS or not tts_enabled(lang):
        return {}
    return {
        node_id: f"/api/tts/audio?lang={lang}&node={node_id}"
        for node_id, text in texts.items()
        if text and narration_text(lang, node_id)
    }


@app.get("/api/tts/audio")
def tts_audio(lang: str = Query(...), node: str = Query(...)):
    if lang not in VALID_LANGS:
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
    if lang not in VALID_LANGS:
        raise HTTPException(400, detail=f"lang must be one of {sorted(VALID_LANGS)}")
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


class ReviewPayload(BaseModel):
    rating: int
    experience: str
    improvements: str
    lang: str
    ethnicity: str
    chosen_option: str


@app.post("/api/review")
def submit_review(body: ReviewPayload):
    if body.rating < 1 or body.rating > 5:
        raise HTTPException(400, detail="rating must be 1-5")
    import datetime
    entry = body.model_dump()
    entry["timestamp"] = datetime.datetime.utcnow().isoformat()
    # Always log the review so it shows up in Vercel's function logs.
    print("REVIEW " + json.dumps(entry, ensure_ascii=False), flush=True)
    # On Vercel the disk is temporary, so the file copy is not permanent there.
    reviews_path = (Path(tempfile.gettempdir()) if ON_VERCEL else BASE_DIR) / "reviews.json"
    existing = []
    if reviews_path.exists():
        existing = json.loads(reviews_path.read_text())
    existing.append(entry)
    with open(reviews_path, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
    return {"ok": True}


if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    @app.get("/")
    def serve_index():
        return FileResponse(str(DIST_DIR / "index.html"))

    @app.get("/{full_path:path}")
    def catch_all(full_path: str):
        return FileResponse(str(DIST_DIR / "index.html"))
