from pathlib import Path
from fastapi import FastAPI, Query, HTTPException, Header, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Any
import json, copy, os, re, sys, tempfile
from dotenv import load_dotenv
from tts import get_audio, tts_enabled
import localize
import simgen
import store
import sims
import media_gen

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")  # backend/.env
load_dotenv(BASE_DIR.parent / ".env.local", override=False)  # project root .env.local (ElevenLabs key)
DIST_DIR = BASE_DIR.parent / "dist"
ON_VERCEL = bool(os.getenv("VERCEL"))

app = FastAPI(title="SchoolSims Player")


@app.middleware("http")
async def _load_looks(request, call_next):
    if request.url.path.startswith("/api/"):
        refresh_personas()
    return await call_next(request)

with open(BASE_DIR / "scenario.json") as f:
    SCENARIO: dict = json.load(f)



def valid_langs() -> set[str]:
    """Approved languages = language blocks in scenario.json (grows when a draft is approved)."""
    return {k for k, v in SCENARIO.items() if isinstance(v, dict) and not k.startswith("_") and "nodes" in v}


# Characters (look, names per language, photo, videos) live in personas.json.
with open(BASE_DIR / "personas.json") as f:
    BUILTIN_PERSONAS: dict = json.load(f)["personas"]

# Looks added in the admin (Character looks) are kept in the runtime store and merged
# in front of the built-in ones (so a new look for a language is suggested first).
LOOKS_HASH = "looks"
PERSONAS: dict = dict(BUILTIN_PERSONAS)
VALID_ETHNICITIES = set(PERSONAS)
_looks_loaded_at = 0.0


def refresh_personas(force: bool = False) -> None:
    global _looks_loaded_at
    import time as _t
    if not force and _t.time() - _looks_loaded_at < 30:
        return
    try:
        custom = store.hget_all(LOOKS_HASH)
    except Exception as e:  # storage hiccup: keep what we have
        print(f"looks: {e}", file=sys.stderr)
        return
    merged = {k: v for k, v in custom.items() if isinstance(v, dict)}
    merged.update(BUILTIN_PERSONAS)
    PERSONAS.clear()
    PERSONAS.update(merged)
    VALID_ETHNICITIES.clear()
    VALID_ETHNICITIES.update(PERSONAS)
    _looks_loaded_at = _t.time()


def look_label(pid: str, ui: dict | None = None) -> str:
    """Label of a look in a language: the sim's own labels for built-in looks,
    the admin-given label (per language if set) for added ones."""
    p = PERSONAS.get(pid, {})
    if p.get("custom"):
        labels = p.get("labels") or {}
        lang = (ui or {}).get("speech_locale", "")
        return labels.get(lang) or labels.get(lang.split("-")[0]) or p.get("label", pid)
    return (ui or SCENARIO["en"]["ui"]).get("ethnicity_labels", {}).get(pid, pid)
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
    # An exact match wins (sw-TZ), then the base language (es-MX -> es, en-GB -> en).
    for c in dict.fromkeys([lang, lang.split("-")[0]]):
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


# ---------------------------------------------------------------------------
# Sim library: "default" is scenario.json; others come from the AI builder (sims.py)
# ---------------------------------------------------------------------------

def builtin_record() -> dict:
    return {"id": sims.BUILTIN_ID, "title": SCENARIO["en"]["ui"].get("sim_title", "Leadership Decision Simulation"),
            "status": "published", "builtin": True, "created_by": "SchoolSims", "created_at": "",
            "context_summary": SCENARIO["en"]["ui"].get("brief_scenario_body", ""), "scenario": SCENARIO}


def sim_langs(scenario: dict) -> set[str]:
    return {k for k, v in scenario.items() if isinstance(v, dict) and not k.startswith("_") and "nodes" in v}


def visible_langs(rec: dict, token: str | None = None) -> set[str]:
    """Languages learners can pick. A language added to a published sim waits for a
    reviewer (pending_langs); admins can still preview it."""
    langs = sim_langs(rec["scenario"])
    if is_admin_token(token):
        return langs
    return langs - set(rec.get("pending_langs") or [])


def locale_place(code: str) -> str:
    """Where a language's regional version is set (from the built-in sim), e.g. 'Lalitpur, Nepal'."""
    setting = SCENARIO.get("_locale_meta", {}).get(code, {}).get("setting", "")
    return setting.split(" — ")[0].strip()


def is_admin_token(token: str | None) -> bool:
    expected = [t for t in (os.getenv("ADMIN_TOKEN"), os.getenv("REVIEWER_TOKEN")) if t]
    if expected:
        return token in expected
    return not ON_VERCEL  # local / Codespace: open


def load_sim(sim_id: str, token: str | None = None) -> dict:
    """A playable sim record. Unpublished sims only open with an admin token (preview)."""
    if not sim_id or sim_id == sims.BUILTIN_ID:
        return builtin_record()
    sim = sims.get(sim_id)
    if not sim:
        raise HTTPException(404, detail="Simulation not found.")
    if sim.get("status") != "published" and not is_admin_token(token):
        raise HTTPException(404, detail="Simulation not found.")
    return sim


def has_video_slides(scenario: dict) -> bool:
    return any(n.get("type") == "video" for n in scenario.get("en", {}).get("nodes", {}).values())


def combo_ok(pid: str, lang: str, needs_video: bool) -> bool:
    return playable(pid, lang) if needs_video else True


@app.get("/api/sims")
def list_published_sims(lang: str = Query("en")):
    """Sims a learner can pick, titled in the requested language when available."""
    out = []
    for rec in [builtin_record()] + sorted(
            (s for s in sims.all_generated() if s.get("status") == "published"),
            key=lambda s: s.get("reviewed_at", ""), reverse=True):
        sc = rec["scenario"]
        langs = visible_langs(rec)
        block = sc.get(pick({c: c for c in langs}, lang) or "en", sc["en"])
        out.append({"id": rec["id"], "title": block["ui"].get("sim_title", rec["title"]),
                    "summary": block["ui"].get("brief_scenario_body", ""), "languages": sorted(langs)})
    return out


@app.get("/api/options")
def get_options(lang: str = Query("en"), sim: str = Query(sims.BUILTIN_ID),
                x_admin_token: str | None = Header(default=None)):
    rec = load_sim(sim, x_admin_token)
    sc = rec["scenario"]
    langs = visible_langs(rec, x_admin_token)
    display_lang = lang if lang in langs else "en"
    ui = sc[display_lang]["ui"]
    # Only the built-in sim needs a ready-made clip per look; AI-built sims make clips per slide.
    needs_video = bool(rec.get("builtin")) and has_video_slides(sc)

    def label(code: str) -> str:
        return (ui.get("language_labels", {}).get(code) or sc[code]["ui"].get("language_labels", {}).get(code)
                or (SCENARIO[display_lang]["ui"].get("language_labels", {}).get(code) if display_lang in SCENARIO else None)
                or code)

    def suggested(code: str) -> str | None:
        for c in dict.fromkeys([code, code.split("-")[0]]):
            for pid, p in PERSONAS.items():
                if c in p.get("suggested_for_langs", []) and combo_ok(pid, code, needs_video):
                    return pid
        return rec.get("default_look") if rec.get("default_look") in PERSONAS else None

    return {
        "sim": rec["id"],
        "languages": [{"value": code, "label": label(code)} for code in sorted(langs)],
        "ethnicities": [
            {"value": code, "label": look_label(code, ui)}
            for code in sorted(VALID_ETHNICITIES, key=lambda c: (bool(PERSONAS[c].get("custom")), c))
        ],
        "available_combos": sorted([pid, code] for pid in PERSONAS for code in langs if combo_ok(pid, code, needs_video)),
        "suggested": {code: suggested(code) for code in sorted(langs)},
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
    lang: str = Query(..., description="locale code, e.g. en or es-MX"),
    ethnicity: str = Query(..., description="persona id from personas.json"),
    sim: str = Query(sims.BUILTIN_ID),
    x_admin_token: str | None = Header(default=None),
):
    rec = load_sim(sim, x_admin_token)
    sc = rec["scenario"]
    if lang not in visible_langs(rec, x_admin_token):
        raise HTTPException(400, detail=f"lang must be one of {sorted(visible_langs(rec, x_admin_token))}")
    if ethnicity not in VALID_ETHNICITIES:
        raise HTTPException(400, detail=f"ethnicity must be one of {sorted(VALID_ETHNICITIES)}")

    persona = PERSONAS[ethnicity]
    name = persona_name(ethnicity, lang)
    nodes = swap_name(copy.deepcopy(sc[lang]["nodes"]), name)
    for node in nodes.values():
        if node.get("character_name") == name or node.get("character_name") == PLACEHOLDER_NAME:
            node["character_name"] = name
    for nid, url in (rec.get("images") or {}).items():
        if nid in nodes:
            nodes[nid]["image_url"] = url
    if not rec.get("builtin"):
        # Each video slide of an AI-built sim has its own clip per look + language;
        # fall back to the base language, then English, with the same character.
        clips = rec.get("videos") or {}
        for nid, node in nodes.items():
            if node.get("type") != "video":
                continue
            node["own_video"] = True
            for c in lang_chain(lang):
                v = clips.get(f"{nid}|{ethnicity}|{c}")
                if v and v.get("status") == "ready":
                    node["video_url"], node["captions_url"] = v["url"], v.get("captions")
                    break
    photos = persona.get("photo", {})
    videos = persona.get("video", {})
    video = pick(videos, lang, "")
    first_char = next((n for n in nodes.values() if n.get("type") == "text_image"), {})

    return {
        "sim": rec["id"],
        "start_node_id": sc["start_node_id"],
        "nodes": nodes,
        "ui": {**swap_name(sc[lang]["ui"], name),
               "ethnicity_labels": {**sc[lang]["ui"].get("ethnicity_labels", {}),
                                    **{pid: look_label(pid, sc[lang]["ui"]) for pid, p in PERSONAS.items() if p.get("custom")}}},
        "media": {
            "photo": pick(photos, lang) or photos.get("default", ""),
            "video_template": video,
            "photo_alt": first_char.get("character_alt", name),
            "character_name": first_char.get("character_name", name),
            # True when the video is in this language or its base language (not English fallback).
            "has_video": any(videos.get(c) for c in lang_chain(lang)[:2]),
            "base_lang": lang.split("-")[0],
            # Subtitles made for this exact clip (scripts/make_videos.py); else the language's file.
            "captions": pick(persona.get("captions", {}), lang),
        },
    }


class ScenarioUpdate(BaseModel):
    start_node_id: str
    nodes: dict[str, Any]
    ui: dict[str, Any]


def narration_text(lang: str, node_id: str, sim: str = sims.BUILTIN_ID) -> str:
    """Build a slide's narration server-side (same rules the frontend uses), so the
    public audio endpoint can only ever voice scenario text, not arbitrary input."""
    if sim and sim != sims.BUILTIN_ID:
        rec = sims.get(sim)
        node = (rec or {}).get("scenario", {}).get(lang, {}).get("nodes", {}).get(node_id)
        return simgen.narration_text(node) if node else ""
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
    sim: str = body.get("sim") or sims.BUILTIN_ID
    if not tts_enabled(lang):
        return {}
    q = "" if sim == sims.BUILTIN_ID else f"&sim={sim}"
    return {
        node_id: f"/api/tts/audio?lang={lang}&node={node_id}{q}"
        for node_id, text in texts.items()
        if text and narration_text(lang, node_id, sim)
    }


@app.get("/api/tts/audio")
def tts_audio(lang: str = Query(...), node: str = Query(...), sim: str = Query(sims.BUILTIN_ID)):
    text = narration_text(lang, node, sim)
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
    sim: str = "default"
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
def require_admin(x_admin_token: str | None = Header(default=None)):
    """Builders (ADMIN_TOKEN) and reviewers (REVIEWER_TOKEN) can use the admin."""
    expected = [t for t in (os.getenv("ADMIN_TOKEN"), os.getenv("REVIEWER_TOKEN")) if t]
    if expected:
        if x_admin_token not in expected:
            raise HTTPException(401, detail="Admin token required.")
    elif ON_VERCEL:
        raise HTTPException(403, detail="Set ADMIN_TOKEN to use the admin page on a hosted site.")


def is_reviewer(token: str | None) -> bool:
    """Publishing needs REVIEWER_TOKEN when it is set; otherwise any admin can publish."""
    reviewer = os.getenv("REVIEWER_TOKEN")
    return token == reviewer if reviewer else True


def require_reviewer(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    if not is_reviewer(x_admin_token):
        raise HTTPException(403, detail="Only SchoolSims reviewers can approve or send back sims.")


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


# ---------------------------------------------------------------------------
# Admin: AI sim builder ("Create from AI"), Extend Sim, review, report
# ---------------------------------------------------------------------------

class Character(BaseModel):
    name: str = ""
    role: str = ""


class Featured(BaseModel):
    role: str = ""
    name: str = ""          # blank = name matches the learner's chosen language/look
    ethnicity: str = ""     # default look (persona id); learners can still change it


class SimInputs(BaseModel):
    title: str
    audience: str = ""
    context: str
    objective: str = ""
    tone: str = "Realistic / challenging"
    setting: str = ""
    decisions: int = 3
    options_per_decision: int = 3
    total_tracks: int = 6
    slides_per_track: int = 2
    pros_cons: bool = True
    follow_up_tracks: bool = True
    summary: bool = True
    video_slides: str = "tracks"   # "tracks" (intro + every track) | "intro" | "none"
    featured: Featured = Featured()
    characters: list[Character] = []
    language: str = "en"
    language_name: str = ""
    accent: str = ""
    notes: str = ""
    created_by: str = ""
    creator_email: str = ""


class ExtendInputs(BaseModel):
    decisions: int = 1
    options_per_decision: int = 3
    total_tracks: int = 2
    slides_per_track: int = 1
    pros_cons: bool = True
    follow_up_tracks: bool = True
    summary: bool = True
    notes: str = ""
    created_by: str = ""
    creator_email: str = ""


class SimEdits(BaseModel):
    lang: str = "en"
    edits: dict[str, str]   # flattened path ("nodes.d1.prompt") -> text
    title: str | None = None


class ReviewAction(BaseModel):
    reviewer: str = ""
    note: str = ""


class TranslateRequest(BaseModel):
    code: str
    language_name: str = ""
    setting: str = ""
    notes: str = ""


def require_storage():
    """On Vercel each request can land on a different server with its own temporary
    disk, so a sim saved there is gone on the next request. Don't spend API credit
    generating until real storage (Upstash Redis) is connected."""
    if not store.is_persistent():
        seen = store.storage_vars_seen()
        raise HTTPException(503, detail=(
            "Connect storage first: in Vercel open this project's Storage tab, add Upstash "
            "Redis (or Redis) and connect it to this project for Production, then redeploy. "
            "Without it, new sims disappear straight after they are created. "
            + (f"Storage settings this deployment can see: {', '.join(seen)}." if seen
               else "This deployment can't see any storage settings yet; if you already connected "
                    "it, redeploy (Deployments > ... > Redeploy).")))


def base_url(request: Request) -> str:
    return os.getenv("APP_URL") or str(request.base_url).rstrip("/")


def app_ui(code: str) -> dict | None:
    """The app's own interface strings for a language (from the built-in sim), if it has them."""
    for c in lang_chain(code)[:2]:
        if c in SCENARIO and isinstance(SCENARIO[c], dict) and "ui" in SCENARIO[c]:
            return SCENARIO[c]["ui"]
    return None


def editable_sim(sim_id: str) -> dict:
    sim = sims.get(sim_id)
    if not sim:
        raise HTTPException(404, detail="Sim not found.")
    if prune_empty_skips(sim):
        sims.save(sim)
    return sim


def prune_empty_skips(sim: dict) -> bool:
    """Older translations flagged empty English fields (an outcome's you_chose, which the
    player fills in) as 'Model skipped'. Those aren't problems: drop them."""
    en_flat = localize.flatten(sim.get("scenario", {}).get("en", {}))
    keep = []
    for p in sim.get("problems", []):
        m = re.match(r"(?:\[[^\]]+\] )?Model skipped: (\S+)$", p)
        if m and not en_flat.get(m.group(1), "x").strip():
            continue
        keep.append(p)
    changed = len(keep) != len(sim.get("problems", []))
    sim["problems"] = keep
    return changed


def sim_detail(sim: dict) -> dict:
    return {**sims.summary(sim), "inputs": sim.get("inputs", {}), "scenario": sim.get("scenario", {}),
            "images": sim.get("images", {}), "review_notes": sim.get("review_notes", []),
            "problems": sim.get("problems", []), "translation_notes": sim.get("translation_notes", {}),
            "ada": simgen.ada_check(sim.get("scenario", {}), tts_enabled("en")),
            "default_look": sim.get("default_look", ""), "accent": sim.get("accent", ""),
            "pending_langs": sim.get("pending_langs") or [],
            "media": media_summary(sim) if not sim.get("builtin") else None}


def _gen_error(e: Exception):
    if isinstance(e, ValueError):
        raise HTTPException(400, detail=str(e))
    print(f"Sim generation failed: {e}", file=sys.stderr)
    raise HTTPException(502, detail=f"Generation failed: {e}")


@app.get("/api/admin/whoami", dependencies=[Depends(require_admin)])
def admin_whoami(request: Request, x_admin_token: str | None = Header(default=None)):
    sims.run_expiry(base_url(request))
    return {"role": "reviewer" if is_reviewer(x_admin_token) else "builder",
            "reviewer_email": sims.REVIEWER_EMAIL, "generator_ready": simgen.generator_ready(),
            "images_ready": media_gen.images_ready(), "email_ready": sims.email_ready(),
            "heygen_ready": media_gen.heygen_ready(), "blob_ready": media_gen.blob_ready(),
            "storage": store.backend_name(), "persistent": store.is_persistent(),
            "review_days": sims.REVIEW_DAYS,
            "personas": [{"id": k, "label": look_label(k),
                          "photo": v.get("photo", {}).get("default", "")} for k, v in PERSONAS.items()],
            "app_languages": sorted(c for c in valid_langs()),
            "language_options": [
                {"code": c, "label": language_label(c, "en"), "native": language_label(c, c), "place": locale_place(c)}
                for c in sorted(valid_langs())]}


@app.get("/api/admin/sims", dependencies=[Depends(require_admin)])
def admin_list_sims(request: Request):
    sims.run_expiry(base_url(request))
    rows = [sims.summary(builtin_record())] + sorted(
        (sims.summary(s) for s in sims.all_generated()), key=lambda r: r["created_at"], reverse=True)
    return {"sims": rows}


@app.post("/api/admin/sims", dependencies=[Depends(require_admin)])
def admin_create_sim(body: SimInputs):
    require_storage()
    if not simgen.generator_ready():
        raise HTTPException(503, detail="Set ANTHROPIC_API_KEY to generate sims.")
    inp = body.model_dump()
    if body.language != "en" and not localize.CODE_RE.match(body.language):
        raise HTTPException(400, detail="Language code must look like 'es' or 'es-MX'.")
    try:
        result = simgen.generate(inp, SCENARIO["en"]["ui"])
    except Exception as e:
        _gen_error(e)
    t = sims.iso(sims.now())
    sim = {
        "id": sims.new_id(result["title"]), "title": result["title"], "status": "draft", "builtin": False,
        "source": "ai", "parent_id": None, "created_by": body.created_by.strip(),
        "creator_email": body.creator_email.strip(), "created_at": t,
        "inputs": result["inputs"], "context_summary": result["context_summary"],
        "review_notes": result["review_notes"], "problems": result["problems"],
        "default_look": body.featured.ethnicity if body.featured.ethnicity in PERSONAS else "",
        "accent": body.accent, "model": result["model"], "scenario": result["scenario"], "images": {},
    }
    # Built in another language: English stays the master (fallback), and the
    # requested language is drafted from it straight away.
    if body.language and body.language != "en":
        try:
            tr = simgen.translate(sim["scenario"], body.language, body.language_name,
                                  body.setting or locale_place(body.language),
                                  f"Accent/variety: {body.accent}" if body.accent else "", app_ui(body.language))
            sim["scenario"][body.language] = tr["block"]
            sim["problems"] += tr["problems"]
            sim["translation_notes"] = {body.language: tr["review_notes"]}
        except Exception as e:
            sim["problems"].append(f"Couldn't draft {body.language}: {e}")
    sims.save(sim)
    return sim_detail(sim)


@app.get("/api/admin/sims/{sim_id}", dependencies=[Depends(require_admin)])
def admin_get_sim(sim_id: str):
    if sim_id == sims.BUILTIN_ID:
        return sim_detail(builtin_record())
    return sim_detail(editable_sim(sim_id))


@app.put("/api/admin/sims/{sim_id}", dependencies=[Depends(require_admin)])
def admin_edit_sim(sim_id: str, body: SimEdits):
    sim = editable_sim(sim_id)
    if sim["status"] == "published" and body.lang not in (sim.get("pending_langs") or []):
        raise HTTPException(409, detail="Published sims can't be edited. Unpublish it first, or extend it.")
    block = sim["scenario"].get(body.lang)
    if not block:
        raise HTTPException(400, detail="That language isn't in this sim.")
    allowed = localize.flatten(block)
    for path, text in body.edits.items():
        if path not in allowed:
            raise HTTPException(400, detail=f"Can't edit {path}.")
        localize.set_path(block, path, text)
    if body.title is not None and body.title.strip():
        sim["title"] = body.title.strip()
        block["ui"]["sim_title"] = body.title.strip()
    # Text edits never change the branching; they can fill fields the model skipped.
    filled = {f"Model skipped: {p}" for p in body.edits} | {f"[{body.lang}] Model skipped: {p}" for p in body.edits}
    sim["problems"] = [p for p in sim.get("problems", []) if p not in filled]
    sims.save(sim)
    return sim_detail(sim)


@app.delete("/api/admin/sims/{sim_id}", dependencies=[Depends(require_admin)])
def admin_delete_sim(sim_id: str):
    sim = editable_sim(sim_id)
    if sim["status"] == "published":
        raise HTTPException(409, detail="Unpublish it before deleting.")
    sims.delete(sim_id)
    return {"ok": True}


@app.post("/api/admin/sims/{sim_id}/problems/clear", dependencies=[Depends(require_admin)])
def admin_clear_problem(sim_id: str, body: dict):
    """Mark model notes as handled: one ("problem"), all of one language ("lang"), or all ("all")."""
    sim = editable_sim(sim_id)
    if body.get("all"):
        sim["problems"] = []
    elif body.get("lang"):
        sim["problems"] = [p for p in sim.get("problems", []) if not p.startswith(f"[{body['lang']}]")]
    else:
        sim["problems"] = [p for p in sim.get("problems", []) if p != body.get("problem")]
    sims.save(sim)
    return sim_detail(sim)


@app.post("/api/admin/sims/{sim_id}/submit", dependencies=[Depends(require_admin)])
def admin_submit_sim(sim_id: str, request: Request):
    sim = editable_sim(sim_id)
    if sim["status"] not in ("draft", "expired"):
        raise HTTPException(409, detail=f"Can't submit a sim that is {sim['status']}.")
    graph = simgen.check_graph(sim["scenario"]["en"]["nodes"], sim["scenario"]["start_node_id"])
    if graph:
        raise HTTPException(400, detail="Fix the branching first: " + "; ".join(graph[:3]))
    ada = simgen.ada_check(sim["scenario"], tts_enabled("en"))
    if not ada["passed"]:
        raise HTTPException(400, detail="Accessibility check failed: " + "; ".join(ada["issues"][:3]))
    return sim_detail(sims.submit(sim, base_url(request)))


@app.post("/api/admin/sims/{sim_id}/approve", dependencies=[Depends(require_reviewer)])
def admin_approve_sim(sim_id: str, body: ReviewAction):
    sim = editable_sim(sim_id)
    if sim["status"] != "pending_review":
        raise HTTPException(409, detail="Only sims waiting for review can be published.")
    if sim.get("problems"):
        raise HTTPException(400, detail="Resolve or dismiss the open problems first.")
    return sim_detail(sims.publish(sim, body.reviewer.strip()))


@app.post("/api/admin/sims/{sim_id}/send-back", dependencies=[Depends(require_reviewer)])
def admin_send_back_sim(sim_id: str, body: ReviewAction):
    sim = editable_sim(sim_id)
    if sim["status"] != "pending_review":
        raise HTTPException(409, detail="Only sims waiting for review can be sent back.")
    if not body.note.strip():
        raise HTTPException(400, detail="Say what needs to change.")
    return sim_detail(sims.send_back(sim, body.reviewer.strip(), body.note.strip()))


@app.post("/api/admin/sims/{sim_id}/unpublish", dependencies=[Depends(require_reviewer)])
def admin_unpublish_sim(sim_id: str):
    sim = editable_sim(sim_id)
    if sim["status"] not in ("published", "superseded"):
        raise HTTPException(409, detail="Not published.")
    return sim_detail(sims.unpublish(sim))


@app.post("/api/admin/sims/{sim_id}/extend", dependencies=[Depends(require_admin)])
def admin_extend_sim(sim_id: str, body: ExtendInputs):
    """Extend Sim: append AI-written decision points to an existing sim. The result is a
    new draft (the original keeps playing until the extension is reviewed and published)."""
    require_storage()
    if not simgen.generator_ready():
        raise HTTPException(503, detail="Set ANTHROPIC_API_KEY to extend sims.")
    base = builtin_record() if sim_id == sims.BUILTIN_ID else editable_sim(sim_id)
    try:
        result = simgen.extend(base["scenario"], body.model_dump())
    except Exception as e:
        _gen_error(e)
    t = sims.iso(sims.now())
    title = f"{base['title']} (extended)"
    sim = {
        "id": sims.new_id(title), "title": title, "status": "draft", "builtin": False,
        "source": "extend", "parent_id": base["id"], "created_by": body.created_by.strip(),
        "creator_email": body.creator_email.strip(), "created_at": t,
        "inputs": {**base.get("inputs", {}), "extension": result["inputs"]},
        "context_summary": base.get("context_summary", ""), "review_notes": result["review_notes"],
        "problems": result["problems"], "default_look": base.get("default_look", ""),
        "accent": base.get("accent", ""), "scenario": result["scenario"],
        "images": dict(base.get("images", {})), "model": simgen.DEFAULT_MODEL,
    }
    sim["scenario"]["en"]["ui"]["sim_title"] = title
    dropped = sorted(sim_langs(base["scenario"]) - {"en"})
    if dropped:
        sim["review_notes"].append("Other languages need to be redone for the new slides: " + ", ".join(dropped))
    sims.save(sim)
    return sim_detail(sim)


@app.post("/api/admin/sims/{sim_id}/languages", dependencies=[Depends(require_admin)])
def admin_translate_sim(sim_id: str, body: TranslateRequest, request: Request):
    """Add (or redo) one language. On a published sim the language waits for a
    reviewer before learners see it; the rest of the sim stays live."""
    require_storage()
    sim = editable_sim(sim_id)
    if not simgen.generator_ready():
        raise HTTPException(503, detail="Set ANTHROPIC_API_KEY to translate sims.")
    code = body.code.strip()
    if code == "en":
        raise HTTPException(400, detail="English is the master version.")
    setting = body.setting.strip() or locale_place(code)
    name = body.language_name.strip() or (language_label(code, code) if code in valid_langs() else "")
    try:
        tr = simgen.translate(sim["scenario"], code, name, setting, body.notes.strip(), app_ui(code))
    except Exception as e:
        _gen_error(e)
    sim = editable_sim(sim_id)  # re-read: other languages may have been added meanwhile
    sim["scenario"][code] = tr["block"]
    sim["problems"] = [p for p in sim.get("problems", []) if not p.startswith(f"[{code}]")] + \
        [f"[{code}] {p}" for p in tr["problems"]]
    sim.setdefault("translation_notes", {})[code] = tr["review_notes"]
    if sim["status"] == "published":
        pending = set(sim.get("pending_langs") or [])
        if code not in pending:
            pending.add(code)
            sims.notify(sims.REVIEWER_EMAIL, f"New language to review: {sim['title']} ({code})",
                        f"{name or code} was added to the published sim \"{sim['title']}\". Learners won't see it "
                        f"until it's approved: {base_url(request)}/admin#sim={sim['id']}")
        sim["pending_langs"] = sorted(pending)
    sims.save(sim)
    return sim_detail(sim)


@app.post("/api/admin/sims/{sim_id}/languages/{code}/approve", dependencies=[Depends(require_reviewer)])
def admin_approve_language(sim_id: str, code: str):
    sim = editable_sim(sim_id)
    if code not in (sim.get("pending_langs") or []):
        raise HTTPException(409, detail="That language isn't waiting for review.")
    if any(p.startswith(f"[{code}]") for p in sim.get("problems", [])):
        raise HTTPException(400, detail=f"Resolve the {code} problems first.")
    sim["pending_langs"] = [c for c in sim["pending_langs"] if c != code]
    sims.save(sim)
    return sim_detail(sim)


@app.delete("/api/admin/sims/{sim_id}/languages/{code}", dependencies=[Depends(require_admin)])
def admin_remove_language(sim_id: str, code: str):
    sim = editable_sim(sim_id)
    if code == "en" or code not in sim["scenario"]:
        raise HTTPException(400, detail="Can't remove that language.")
    if sim["status"] == "published" and code not in (sim.get("pending_langs") or []):
        raise HTTPException(409, detail="Unpublish first.")
    del sim["scenario"][code]
    sim["pending_langs"] = [c for c in sim.get("pending_langs") or [] if c != code]
    sim["problems"] = [p for p in sim.get("problems", []) if not p.startswith(f"[{code}]")]
    sims.save(sim)
    return sim_detail(sim)


@app.post("/api/admin/sims/{sim_id}/images/{node_id}", dependencies=[Depends(require_admin)])
def admin_generate_image(sim_id: str, node_id: str):
    """Create the illustration for one slide from its image prompt (OpenAI image API)."""
    sim = editable_sim(sim_id)
    require_storage()
    if not media_gen.images_ready():
        raise HTTPException(503, detail="Set OPENAI_API_KEY in Vercel to create images.")
    node = sim["scenario"]["en"]["nodes"].get(node_id)
    if not node or not node.get("image_prompt"):
        raise HTTPException(400, detail="That slide has no image prompt.")
    try:
        url = media_gen.make_image(node["image_prompt"], sim_id, node_id)
    except Exception as e:
        raise HTTPException(502, detail=str(e))
    sim = editable_sim(sim_id)  # re-read: other images may have finished meanwhile
    sim.setdefault("images", {})[node_id] = url
    sims.save(sim)
    return sim_detail(sim)


# ---- Videos for AI-built sims (HeyGen -> Vercel Blob) -------------------------------

def video_script(sim: dict, node_id: str, persona: str, lang: str) -> str:
    node = sim["scenario"].get(lang, {}).get("nodes", {}).get(node_id) or {}
    return swap_name(node.get("script", ""), persona_name(persona, lang))


def media_summary(sim: dict) -> dict:
    """Video status per language x look, plus image counts, for the admin."""
    en = sim["scenario"]["en"]["nodes"]
    vnodes = [k for k, n in en.items() if n.get("type") == "video"]
    clips = sim.get("videos") or {}
    langs = sorted(sim_langs(sim["scenario"]))
    grid: dict = {}
    for lang in langs:
        for pid in PERSONAS:
            cell = {"ready": 0, "rendering": 0, "failed": 0, "stale": 0, "total": len(vnodes), "errors": []}
            for nid in vnodes:
                v = clips.get(f"{nid}|{pid}|{lang}")
                if not v:
                    continue
                if v["status"] == "ready" and v.get("script_hash") != media_gen.script_hash(video_script(sim, nid, pid, lang)):
                    cell["stale"] += 1
                elif v["status"] in cell:
                    cell[v["status"]] += 1
                if v.get("status") == "failed" and v.get("error"):
                    cell["errors"].append(f"{nid}: {v['error']}")
            grid.setdefault(lang, {})[pid] = cell
    words = sum(len((n.get("script") or "").split()) for k, n in en.items() if k in vnodes)
    img_nodes = [k for k, n in en.items() if n.get("image_prompt")]
    return {
        "video_nodes": vnodes, "grid": grid,
        "seconds_per_set": round(words / 2.5),  # ~150 spoken words a minute
        "suggested": {lang: (next((pid for pid, p in PERSONAS.items()
                                   if lang in p.get("suggested_for_langs", []) or lang.split("-")[0] in p.get("suggested_for_langs", [])), None)
                             or sim.get("default_look") or "caucasian") for lang in langs},
        "images": {"total": len(img_nodes), "done": sum(1 for k in img_nodes if k in (sim.get("images") or {})),
                   "missing": [k for k in img_nodes if k not in (sim.get("images") or {})]},
        "heygen_ready": media_gen.heygen_ready(), "blob_ready": media_gen.blob_ready(),
        "images_ready": media_gen.images_ready(),
        "looks": [{"id": k, "label": look_label(k), "gender": p.get("gender", "")} for k, p in PERSONAS.items()],
    }


class VideoRequest(BaseModel):
    persona: str
    lang: str
    force: bool = False


@app.get("/api/admin/sims/{sim_id}/media", dependencies=[Depends(require_admin)])
def admin_sim_media(sim_id: str):
    return media_summary(editable_sim(sim_id))


@app.post("/api/admin/sims/{sim_id}/videos", dependencies=[Depends(require_admin)])
def admin_start_videos(sim_id: str, body: VideoRequest, request: Request):
    """Start HeyGen renders for every video slide in one language with one look."""
    require_storage()
    if not media_gen.heygen_ready():
        raise HTTPException(503, detail="Set HEYGEN_API_KEY in Vercel to make videos.")
    if not media_gen.blob_ready():
        raise HTTPException(503, detail="Add Vercel Blob storage (Storage tab -> Blob -> connect) to keep the videos.")
    sim = editable_sim(sim_id)
    if body.persona not in PERSONAS or body.lang not in sim["scenario"]:
        raise HTTPException(400, detail="Unknown look or language for this sim.")
    vnodes = [k for k, n in sim["scenario"]["en"]["nodes"].items() if n.get("type") == "video"]
    if not vnodes:
        raise HTTPException(400, detail="This sim has no video slides.")
    started, errors = 0, []
    hg = media_gen.HeyGen()
    for nid in vnodes:
        key = f"{nid}|{body.persona}|{body.lang}"
        script = video_script(sim, nid, body.persona, body.lang)
        cur = (sim.get("videos") or {}).get(key)
        fresh = cur and cur.get("script_hash") == media_gen.script_hash(script)
        if cur and fresh and cur["status"] in ("ready", "rendering") and not body.force:
            continue
        if not script.strip():
            errors.append(f"{nid}: no script")
            continue
        try:
            entry = media_gen.start_video(script=script, persona_id=body.persona, persona=PERSONAS[body.persona],
                                          lang=body.lang, title=f"{sim['title']} {nid} {body.persona} {body.lang}",
                                          base_url=base_url(request), hg=hg)
            started += 1
        except Exception as e:
            entry = {"status": "failed", "error": str(e)[:300]}
            errors.append(f"{nid}: {e}")
        sim = editable_sim(sim_id)
        sim.setdefault("videos", {})[key] = entry
        sims.save(sim)
        if errors and "credit" in str(errors[-1]).lower():
            break  # out of HeyGen API credit: don't keep trying
    return {**media_summary(editable_sim(sim_id)), "started": started, "errors": errors}


def refresh_videos(sim: dict, limit: int = 6) -> int:
    """Finish up to `limit` renders that HeyGen has completed. Returns how many are still rendering."""
    pending = [k for k, v in (sim.get("videos") or {}).items() if v.get("status") == "rendering"]
    hg = media_gen.HeyGen() if pending else None
    for key in pending[:limit]:
        nid, pid, lang = key.split("|")
        try:
            entry = media_gen.finish_video(sim["videos"][key], script=video_script(sim, nid, pid, lang),
                                           path_prefix=f"sims/{sim['id']}/videos/{nid}-{pid}-{lang}", hg=hg)
        except Exception as e:
            entry = {**sim["videos"][key], "last_error": str(e)[:200]}
        latest = sims.get(sim["id"]) or sim
        latest.setdefault("videos", {})[key] = entry
        sims.save(latest)
        sim = latest
    return sum(1 for v in (sim.get("videos") or {}).values() if v.get("status") == "rendering")


@app.post("/api/admin/sims/{sim_id}/videos/refresh", dependencies=[Depends(require_admin)])
def admin_refresh_videos(sim_id: str):
    sim = editable_sim(sim_id)
    if not media_gen.heygen_ready():
        return media_summary(sim)
    left = refresh_videos(sim)
    return {**media_summary(editable_sim(sim_id)), "rendering": left}


@app.get("/api/sims/{sim_id}/images/{node_id}")
def sim_image(sim_id: str, node_id: str):
    import base64
    b64 = sims.load_image(sim_id, node_id)
    if not b64:
        raise HTTPException(404, detail="No image.")
    return Response(content=base64.b64decode(b64), media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/admin/sim-report", dependencies=[Depends(require_admin)])
def admin_sim_report(start: str = Query(""), end: str = Query(""), format: str = Query("json")):
    rows = sims.report(start, end)
    if format == "csv":
        import csv, io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Sim name", "Context summary", "Creator", "Created", "Status", "Source", "Languages",
                    "Decisions", "Reviewed by", "Reviewed"])
        for r in rows:
            w.writerow([r["title"], r["context_summary"], r["created_by"], r["created_at"][:10], r["status"],
                        r["source"], " ".join(r["languages"]), r["decisions"], r["reviewed_by"], r["reviewed_at"][:10]])
        return Response(content=buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=sim-report-{start or 'all'}-{end or 'now'}.csv"})
    return {"rows": rows}


@app.get("/api/admin/notifications", dependencies=[Depends(require_admin)])
def admin_notifications():
    return {"items": sims.outbox(), "email_ready": sims.email_ready()}


@app.get("/api/cron/sims")
def cron_sims(request: Request, authorization: str | None = Header(default=None)):
    """Daily: remind and expire pending sims. Vercel cron sends 'Bearer <CRON_SECRET>'."""
    secret = os.getenv("CRON_SECRET")
    if secret and authorization != f"Bearer {secret}":
        raise HTTPException(401, detail="Unauthorized")
    if not secret and ON_VERCEL:
        raise HTTPException(403, detail="Set CRON_SECRET.")
    result = sims.run_expiry(base_url(request))
    if media_gen.heygen_ready() and media_gen.blob_ready():
        for s in sims.all_generated():
            if any(v.get("status") == "rendering" for v in (s.get("videos") or {}).values()):
                try:
                    refresh_videos(s, limit=10)
                except Exception as e:  # keep the cron going
                    print(f"video refresh failed for {s['id']}: {e}", file=sys.stderr)
    return result


# ---------------------------------------------------------------------------
# Admin: character looks (add regions/genders beyond the built-in five)
# ---------------------------------------------------------------------------

class LookBody(BaseModel):
    label: str
    gender: str = "female"
    langs: list[str] = []
    names: dict[str, str] = {}
    labels: dict[str, str] = {}
    photo: str = ""            # data URL (uploaded) - optional when editing
    generate_prompt: str = ""  # or: describe the person and let the image model draw them


class NamesBody(BaseModel):
    label: str
    gender: str = ""
    langs: list[str] = []


def look_view(pid: str, p: dict) -> dict:
    return {"id": pid, "label": look_label(pid), "custom": bool(p.get("custom")), "gender": p.get("gender", ""),
            "langs": p.get("suggested_for_langs", []), "names": p.get("name", {}), "labels": p.get("labels", {}),
            "photo": (p.get("photo") or {}).get("default", ""), "videos": sorted((p.get("video") or {}).keys())}


def save_look_photo(pid: str, data: bytes, mime: str) -> str:
    ext = "png" if "png" in mime else "jpg"
    if media_gen.blob_ready():
        return media_gen.blob_put(f"looks/{pid}.{ext}", data, mime)
    import base64 as _b64
    store.hset("look_photos", pid, {"mime": mime, "b64": _b64.b64encode(data).decode()})
    import secrets as _s
    return f"/api/looks/{pid}/photo?v={_s.token_hex(3)}"


@app.get("/api/looks/{pid}/photo")
def look_photo(pid: str):
    import base64 as _b64
    item = store.hget("look_photos", pid)
    if not item:
        raise HTTPException(404, detail="No photo.")
    return Response(content=_b64.b64decode(item["b64"]), media_type=item.get("mime", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/admin/looks", dependencies=[Depends(require_admin)])
def admin_list_looks():
    refresh_personas(force=True)
    return {"looks": [look_view(k, v) for k, v in PERSONAS.items()],
            "images_ready": media_gen.images_ready(), "blob_ready": media_gen.blob_ready()}


@app.post("/api/admin/looks/suggest-names", dependencies=[Depends(require_admin)])
def admin_suggest_names(body: NamesBody):
    if not simgen.generator_ready():
        raise HTTPException(503, detail="Set ANTHROPIC_API_KEY.")
    try:
        return {"names": simgen.suggest_names(body.label, body.gender, body.langs or ["en"])}
    except Exception as e:
        raise HTTPException(502, detail=str(e)[:300])


def _photo_from_body(pid: str, body: LookBody) -> str | None:
    import base64 as _b64
    if body.photo:
        m = re.match(r"data:(image/[a-z+]+);base64,(.+)$", body.photo, re.S)
        if not m:
            raise HTTPException(400, detail="Photo must be an image.")
        data = _b64.b64decode(m.group(2))
        if len(data) > 4_000_000:
            raise HTTPException(400, detail="Photo is too large (max 4 MB).")
        return save_look_photo(pid, data, m.group(1))
    if body.generate_prompt.strip():
        if not media_gen.images_ready():
            raise HTTPException(503, detail="Set OPENAI_API_KEY to generate portraits, or upload a photo.")
        try:
            data = media_gen.make_portrait(body.generate_prompt.strip())
        except Exception as e:
            raise HTTPException(502, detail=str(e)[:300])
        return save_look_photo(pid, data, "image/jpeg")
    return None


@app.post("/api/admin/looks", dependencies=[Depends(require_admin)])
def admin_add_look(body: LookBody):
    require_storage()
    if not body.label.strip():
        raise HTTPException(400, detail="Give the look a name (e.g. Middle East).")
    base = re.sub(r"[^a-z0-9]+", "_", f"{body.label}_{body.gender}".lower()).strip("_")[:40] or "look"
    pid, n = base, 1
    while pid in PERSONAS:
        n += 1
        pid = f"{base}_{n}"
    photo = _photo_from_body(pid, body)
    if not photo:
        raise HTTPException(400, detail="Upload a photo or describe the person to generate one.")
    names = {k: v.strip() for k, v in body.names.items() if v.strip()}
    if not names.get("default"):
        names["default"] = next(iter(names.values()), body.label.strip())
    look = {"custom": True, "label": body.label.strip(), "labels": {k: v for k, v in body.labels.items() if v.strip()},
            "gender": body.gender, "suggested_for_langs": body.langs, "name": names,
            "photo": {"default": photo}, "video": {}, "captions": {}, "created_at": sims.iso(sims.now())}
    store.hset(LOOKS_HASH, pid, look)
    refresh_personas(force=True)
    return look_view(pid, look)


@app.put("/api/admin/looks/{pid}", dependencies=[Depends(require_admin)])
def admin_edit_look(pid: str, body: LookBody):
    look = store.hget(LOOKS_HASH, pid)
    if not look:
        raise HTTPException(404, detail="Only added looks can be edited.")
    photo = _photo_from_body(pid, body)
    if photo:
        look["photo"] = {"default": photo}  # new path, so HeyGen gets the new photo
    names = {k: v.strip() for k, v in body.names.items() if v.strip()}
    look.update(label=body.label.strip() or look["label"], gender=body.gender, suggested_for_langs=body.langs,
                labels={k: v for k, v in body.labels.items() if v.strip()},
                name=names or look.get("name", {}))
    store.hset(LOOKS_HASH, pid, look)
    refresh_personas(force=True)
    return look_view(pid, look)


@app.delete("/api/admin/looks/{pid}", dependencies=[Depends(require_admin)])
def admin_delete_look(pid: str):
    if not store.hget(LOOKS_HASH, pid):
        raise HTTPException(404, detail="Only added looks can be removed.")
    store.hdel(LOOKS_HASH, pid)
    refresh_personas(force=True)
    return {"ok": True}


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
