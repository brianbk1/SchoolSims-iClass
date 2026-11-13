"""Locale drafts: AI-generated, human-approved localizations of the scenario.

How it works
------------
* The English scenario is the master. It is flattened into a list of text fields
  ("nodes.context.body", "ui.start_btn", ...). Structural fields (ids, branching,
  impact directions) are never sent to the model.
* Claude rewrites each text field for the target language AND setting (country,
  institution type, money, calendar), and returns an English back-translation of
  each field plus notes for the reviewer.
* The draft is rebuilt from the English structure, so a draft can never break the
  scenario graph. It is stored as a *pending* draft in backend/drafts/<code>.json.
* Learners only ever see approved content (scenario.json). Approving a draft
  merges it into scenario.json.
"""
from __future__ import annotations

import copy
import datetime
import json
import os
import re
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent
DRAFTS_DIR = BASE_DIR / "drafts"

PLACEHOLDER_NAME = "Jordan Chen"

# Keys that are structure, not text. Never localized.
STRUCTURAL_KEYS = {"type", "next_node_id", "id", "target_node_id", "key", "direction", "speech_locale"}

# For a language that already has an approved translation, only the story is
# re-localized; the rest of the UI (buttons, help text) keeps its reviewed wording.
STORY_UI_KEYS = {"brief_scenario_body", "brief_objective_body"}

# The video is pre-recorded, so this line must keep its meaning in every locale.
FIXED_MEANING_PATHS = {"nodes.video.quote"}

DEFAULT_MODEL = "claude-sonnet-5-5"

CODE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


# ---------------------------------------------------------------------------
# Flatten / rebuild
# ---------------------------------------------------------------------------

def flatten(value: Any, prefix: str = "") -> dict[str, str]:
    """Map 'a.b.0.c' -> text for every localizable string."""
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            if k in STRUCTURAL_KEYS:
                continue
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.update(flatten(v, f"{prefix}.{i}"))
    elif isinstance(value, str):
        out[prefix] = value
    return out


def set_path(root: Any, path: str, text: str) -> None:
    parts = path.split(".")
    node = root
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = text
    else:
        node[last] = text


def english_block(scenario: dict) -> dict:
    return {"nodes": scenario["en"]["nodes"], "ui": scenario["en"]["ui"]}


def source_lang(scenario: dict, code: str) -> str | None:
    """Approved language a draft starts from: the language itself, or its base
    language for a regional variant (es-AR starts from es)."""
    if code in scenario:
        return code
    base = code.split("-")[0]
    return base if base in scenario and base != code else None


def paths_to_localize(scenario: dict, code: str) -> dict[str, str]:
    """English text for every field the model must write for this language."""
    en = flatten(english_block(scenario))
    if source_lang(scenario, code):  # existing language or regional variant: story only
        return {p: t for p, t in en.items()
                if p.startswith("nodes.") or p.split(".")[1] in STORY_UI_KEYS}
    return en  # new language: everything


def build_block(scenario: dict, code: str, texts: dict[str, str], language_name: str) -> dict:
    """Rebuild a full {nodes, ui} block for `code` from the English structure."""
    src = source_lang(scenario, code)
    if src:
        block = copy.deepcopy({"nodes": scenario[src]["nodes"], "ui": scenario[src]["ui"]})
    else:
        block = copy.deepcopy(english_block(scenario))
        block["ui"]["speech_locale"] = code
    for path, text in texts.items():
        set_path(block, path, text)
    labels = block["ui"].setdefault("language_labels", {})
    if language_name:
        labels[code] = language_name
    return block


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(scenario: dict, code: str, block: dict) -> list[str]:
    """Problems that must be fixed before a draft can be approved."""
    problems: list[str] = []
    en = english_block(scenario)
    # Structure must match the English master exactly.
    if set(block.get("nodes", {})) != set(en["nodes"]):
        problems.append("Slides don't match the English scenario.")
        return problems
    for nid, en_node in en["nodes"].items():
        node = block["nodes"][nid]
        for k in STRUCTURAL_KEYS & set(en_node):
            if node.get(k) != en_node[k]:
                problems.append(f"{nid}.{k} changed (structure must match English).")
        for list_key in ("choices", "impacts"):
            if list_key in en_node:
                a, b = en_node[list_key], node.get(list_key, [])
                if len(a) != len(b) or any(
                    {k: x[k] for k in STRUCTURAL_KEYS & set(x)} != {k: y.get(k) for k in STRUCTURAL_KEYS & set(x)}
                    for x, y in zip(a, b)
                ):
                    problems.append(f"{nid}.{list_key} structure changed.")
    flat = flatten(block)
    for path in flatten(en):
        if path.startswith("ui.language_labels."):
            continue  # other languages' names may be missing; the app falls back
        if not flat.get(path, "").strip():
            problems.append(f"Empty text: {path}")
    for path, text in flatten(en).items():
        if PLACEHOLDER_NAME in text and PLACEHOLDER_NAME not in flat.get(path, ""):
            problems.append(f"{path} must keep the placeholder name '{PLACEHOLDER_NAME}' (it is swapped for the character's name).")
    return problems


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""You localize a short leadership-decision simulation used for professional learning.

Your job is cultural localization, not just translation. Rewrite each field in the target language so that a learner in the target setting experiences a realistic, local version of the SAME decision.

Must stay the same in every locale:
- The decision and its trade-off: a customer wants a new feature on a tight timeline before signing, the team has limited capacity; option A commits to the deadline, option B proposes a smaller phased pilot. Outcomes keep the same direction (A: revenue up, capacity down, trust at risk; B: revenue slower, capacity protected, trust up).
- The learning objective (near-term revenue vs delivery capacity vs customer trust).
- The field nodes.video.quote must keep its exact meaning ("Can we commit to four weeks?") because a pre-recorded video says that line. Keep "four weeks" wherever the timeline is mentioned.
- The placeholder name "{PLACEHOLDER_NAME}" must be copied exactly, in Latin letters, wherever it appears. The app replaces it with the on-screen character's name.
- Choice labels keep their leading letter ("A ...", "B ...").
- The company the learner leads is called SchoolSims; keep that name.

Should change to fit the setting:
- The customer institution (a realistic type of college/school for that country, and where it is), local currency and a plausible contract size, local academic calendar or holidays that create real time pressure, and concrete local details that make the stakes feel real.

Quality rules:
- Natural, professional language a native speaker would use at work. Match the length and tone of the English.
- Respectful and specific. No stereotypes, no exaggeration, nothing political or religious beyond naming widely observed public holidays.
- Only state local facts you are confident are true. If something should be checked (a currency amount, calendar overlap, institution type), say so in review_notes.
- UI fields (buttons, labels, help text) are translated plainly, not localized.

Return every requested path. For each, give the localized text and a faithful English back-translation of what you wrote (so a reviewer who cannot read the language can check it)."""

TOOL = {
    "name": "submit_localization",
    "description": "Submit the localized text for every requested path.",
    "input_schema": {
        "type": "object",
        "properties": {
            "strings": {
                "type": "object",
                "description": "Map of path -> {text, back_translation}. Must include every requested path.",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "back_translation": {"type": "string"},
                    },
                    "required": ["text", "back_translation"],
                },
            },
            "review_notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Facts or wording a native-speaker reviewer should double-check.",
            },
        },
        "required": ["strings", "review_notes"],
    },
}


def generator_ready() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def call_claude(user_prompt: str) -> dict:
    """Ask Claude for a localization. Isolated so tests can replace it.

    Newer models reject forced tool use, so the tool is offered with tool_choice
    "auto" and the prompt asks for it; one retry if the model answers in text."""
    import anthropic  # lazy: app starts without the SDK or a key

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_prompt + f"\n\nRespond only by calling the {TOOL['name']} tool."}]
    for attempt in range(2):
        # Streamed: the SDK refuses long non-streaming requests.
        with client.messages.stream(
            model=os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL),
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            tools=[TOOL],
            tool_choice={"type": "auto"},
            messages=messages,
        ) as stream:
            msg = stream.get_final_message()
        for part in msg.content:
            if getattr(part, "type", "") == "tool_use" and part.name == TOOL["name"]:
                return part.input
        if attempt == 0:
            messages = messages + [
                {"role": "assistant", "content": [p.model_dump() for p in msg.content if getattr(p, "type", "") == "text"] or "OK."},
                {"role": "user", "content": f"Please call the {TOOL['name']} tool now with every field."},
            ]
    raise RuntimeError("The model did not return a localization.")


def build_prompt(scenario: dict, code: str, language_name: str, setting: str, notes: str) -> str:
    wanted = paths_to_localize(scenario, code)
    src = source_lang(scenario, code)
    current = flatten({"nodes": scenario[src]["nodes"], "ui": scenario[src]["ui"]}) if src else {}
    rows = [{"path": p, "english": t, **({"current_translation": current[p]} if p in current else {})}
            for p, t in wanted.items()]
    return (
        f"Target language: {language_name or code} (code: {code})\n"
        f"Target setting: {setting}\n"
        + (f"Extra guidance from the reviewer: {notes}\n" if notes else "")
        + (f"An approved {src} translation exists; current_translation is given for terminology. "
           "Re-localize the story for the setting, using the regional vocabulary and forms of address "
           "people there actually use (e.g. 'vos' in Argentina).\n" if current else "")
        + "\nFields to write (JSON):\n"
        + json.dumps(rows, ensure_ascii=False, indent=1)
    )


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------

def _draft_path(code: str) -> Path:
    if not CODE_RE.match(code):
        raise ValueError("Language code must look like 'ne', 'es' or 'es-MX'.")
    return DRAFTS_DIR / f"{code}.json"


# On a hosted site (Vercel) the disk is read-only, so drafts live in the runtime
# store (Upstash Redis) instead of backend/drafts/.
def _hosted() -> bool:
    return bool(os.getenv("VERCEL"))


DRAFTS_HASH = "locale_drafts"


def _summary(d: dict) -> dict:
    return ({k: d.get(k) for k in ("code", "language_name", "setting", "status", "created_at", "is_new_language")}
            | {"problems": len(d.get("problems", []))})


def list_drafts() -> list[dict]:
    if _hosted():
        import store
        return [_summary(d) for _, d in sorted(store.hget_all(DRAFTS_HASH).items())]
    if not DRAFTS_DIR.exists():
        return []
    return [_summary(json.loads(f.read_text())) for f in sorted(DRAFTS_DIR.glob("*.json"))]


def load_draft(code: str) -> dict | None:
    p = _draft_path(code)  # validates the code
    if _hosted():
        import store
        return store.hget(DRAFTS_HASH, code)
    return json.loads(p.read_text()) if p.exists() else None


def save_draft(draft: dict) -> None:
    _draft_path(draft["code"])
    if _hosted():
        import store
        store.hset(DRAFTS_HASH, draft["code"], draft)
        return
    DRAFTS_DIR.mkdir(exist_ok=True)
    _draft_path(draft["code"]).write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n")


def delete_draft(code: str) -> None:
    p = _draft_path(code)
    if _hosted():
        import store
        store.hdel(DRAFTS_HASH, code)
        return
    if p.exists():
        p.unlink()


def generate_draft(scenario: dict, code: str, language_name: str, setting: str, notes: str = "",
                   model_call=None) -> dict:
    code = code.strip()
    _draft_path(code)  # validates the code
    if code == "en":
        raise ValueError("English is the master version; edit scenario.json directly.")
    wanted = paths_to_localize(scenario, code)
    result = (model_call or call_claude)(build_prompt(scenario, code, language_name, setting, notes))
    strings = result.get("strings", {}) if isinstance(result, dict) else {}
    if isinstance(strings, str):  # sometimes returned as a JSON string
        try:
            strings = json.loads(strings)
        except ValueError:
            strings = {}
    strings = strings if isinstance(strings, dict) else {}

    texts, back = {}, {}
    for path in wanted:
        item = strings.get(path)
        if isinstance(item, str):
            item = {"text": item}
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            texts[path] = item["text"]
            back[path] = item.get("back_translation", "")
    missing = [p for p in wanted if p not in texts]

    block = build_block(scenario, code, texts, language_name)
    problems = validate(scenario, code, block) + [f"Model skipped: {p}" for p in missing]
    draft = {
        "code": code,
        "language_name": language_name or (scenario.get(code, {}).get("ui", {}).get("language_labels", {}).get(code, code)),
        "setting": setting,
        "notes": notes,
        "is_new_language": source_lang(scenario, code) is None,
        "based_on": source_lang(scenario, code),
        "status": "pending",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "model": os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL),
        "fields": list(wanted),
        "block": block,
        "back_translation": back,
        "review_notes": [str(n) for n in result.get("review_notes", [])] if isinstance(result, dict) else [],
        "skipped": missing,
        "problems": problems,
    }
    save_draft(draft)
    return draft


def update_draft(scenario: dict, code: str, edits: dict[str, str]) -> dict:
    draft = load_draft(code)
    if not draft:
        raise KeyError(code)
    for path, text in edits.items():
        if path in draft["fields"]:
            set_path(draft["block"], path, text)
    draft["skipped"] = [p for p in draft.get("skipped", []) if p not in edits]
    draft["problems"] = validate(scenario, code, draft["block"]) + [f"Model skipped: {p}" for p in draft["skipped"]]
    save_draft(draft)
    return draft
