"""AI sim builder: turn a short brief into a full branching simulation.

Shape of a generated sim (same node format the player already uses):

    intro (context) -> intro_char (text_image) -> d1 (decision)
        option A -> track d1t1 (outcome slide, then optional follow-up slides) --+
        option B -> track d1t2 ...                                               +--> d2 -> ... -> summary
        option C -> track d1t1 (options may share a track)  ---------------------+

Every track ends at the next decision, so all learners meet at the same decision
points; what differs is the track they see and the scores their choices carry.

The model writes the content; this module assigns ids and wires the branches, so
the structure is always valid. `model_call` can be swapped for tests.
"""
from __future__ import annotations

import copy
import json
import os
import re
from typing import Any, Callable

import localize

PLACEHOLDER_NAME = localize.PLACEHOLDER_NAME  # featured character; swapped per language/look
FEATURED_TOKEN = "[[FEATURED]]"
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
LETTERS = "ABCDEF"

LIMITS = {"decisions": (1, 6), "options": (2, 4), "slides_per_track": (1, 3)}


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def tracks_per_decision(total_tracks: int, decisions: int, options: int) -> list[int]:
    """Spread the total number of tracks over the decisions (earlier decisions get
    the extra ones). Each decision has at least 1 track and at most one per option."""
    lo, hi = decisions, decisions * options
    if not lo <= total_tracks <= hi:
        raise ValueError(f"Total tracks must be between {lo} and {hi} for {decisions} decisions "
                         f"with {options} options each.")
    base, extra = divmod(total_tracks, decisions)
    return [base + (1 if i < extra else 0) for i in range(decisions)]


def check_inputs(inp: dict) -> dict:
    """Normalise and validate the builder form. Raises ValueError with a readable message."""
    out = dict(inp)
    for key, (lo, hi) in (("decisions", LIMITS["decisions"]), ("options_per_decision", LIMITS["options"]),
                          ("slides_per_track", LIMITS["slides_per_track"])):
        v = int(out.get(key) or 0)
        if not lo <= v <= hi:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} must be between {lo} and {hi}.")
        out[key] = v
    if not out.get("follow_up_tracks", True):
        out["slides_per_track"] = 1
    out["total_tracks"] = int(out.get("total_tracks") or out["decisions"] * min(2, out["options_per_decision"]))
    out["tracks_split"] = tracks_per_decision(out["total_tracks"], out["decisions"], out["options_per_decision"])
    for key in ("title", "context"):
        if not str(out.get(key, "")).strip():
            raise ValueError(f"{key.capitalize()} is required.")
    out.setdefault("pros_cons", True)
    out.setdefault("summary", True)
    out.setdefault("language", "en")
    return out


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You write branching decision simulations for SchoolSims, a professional-learning platform for K-12 and higher-education leaders and staff.

A simulation puts the learner in a realistic role facing a realistic situation. At each decision point the learner chooses an option; the option leads into a "track" (a short sequence of slides that plays out the consequences), and every track then flows into the next decision point. Options can share a track when their consequences are similar.

Write so that:
- Every option is plausible. Avoid an obviously right answer; good options have real costs, weaker options have something going for them.
- Tracks show realistic consequences: how people react, what changes, what new pressure appears. Later decisions should feel like they follow from what came before, whichever track the learner saw.
- Language is plain, specific and respectful; slide bodies are 2-4 sentences. No jargon the audience wouldn't use.
- Characters and settings are respectful and never stereotyped. Do not assign a gender to the featured character: refer to them by name or role, never "he" or "she".
- The featured character is written as exactly [[FEATURED]] wherever their name appears (the platform swaps in a name that matches the learner's chosen language and region). Use other characters' names exactly as given.
- Images: for slides that should have an illustration, write an image_prompt (a calm, realistic, text-free scene that fits the setting; no real people, no logos) and image_alt (one sentence describing the image for screen-reader users).
- Impacts: score every option on each impact dimension with direction up, down or mid and a one-sentence reason.

Call submit_simulation with the complete simulation. Match the requested counts exactly."""

_IMPACT = {"type": "object", "required": ["key", "direction", "body"], "properties": {
    "key": {"type": "string"}, "direction": {"type": "string", "enum": ["up", "down", "mid"]},
    "body": {"type": "string"}}}
_SLIDE = {"type": "object", "required": ["kind", "title", "body"], "properties": {
    "kind": {"type": "string", "enum": ["outcome", "scene", "character"],
             "description": "First slide of a track is always 'outcome'. 'scene' = narrative slide with an image; 'character' = the featured character speaks/reacts (shown with their portrait)."},
    "title": {"type": "string"}, "body": {"type": "string"},
    "point1": {"type": "string", "description": "character slides: a key fact"},
    "point2": {"type": "string", "description": "character slides: a risk or tension"},
    "image_prompt": {"type": "string"}, "image_alt": {"type": "string"}}}
_DECISION = {"type": "object", "required": ["short_title", "prompt", "options", "track_summaries"], "properties": {
    "short_title": {"type": "string", "description": "2-4 words, e.g. 'Initial response'"},
    "prompt": {"type": "string", "description": "The situation at this point and the question, 1-3 sentences."},
    "options": {"type": "array", "items": {"type": "object", "required": ["label", "track", "impacts"], "properties": {
        "label": {"type": "string", "description": "What the learner does, one sentence, no letter prefix."},
        "track": {"type": "integer", "description": "1-based index into this decision's tracks."},
        "pros": {"type": "array", "items": {"type": "string"}},
        "cons": {"type": "array", "items": {"type": "string"}},
        "impacts": {"type": "array", "items": _IMPACT}}}},
    "track_summaries": {"type": "array", "items": {"type": "string"},
                        "description": "One sentence per track: what happens on it. The slides are written later."}}}
_SUMMARY = {"type": "object", "required": ["title", "body", "question"], "properties": {
    "title": {"type": "string"}, "body": {"type": "string", "description": "Debrief: what the decisions were really about."},
    "question": {"type": "string", "description": "One reflection question."},
    "takeaways": {"type": "array", "items": {"type": "string"}}}}

TOOL = {
    "name": "submit_simulation",
    "description": "Submit the complete simulation.",
    "input_schema": {"type": "object", "required": ["title", "context_summary", "impact_dimensions", "intro",
                                                     "featured_slide", "decisions", "summary"], "properties": {
        "title": {"type": "string"},
        "context_summary": {"type": "string", "description": "1-2 sentences for the content library and reports."},
        "learner_role": {"type": "string"},
        "impact_dimensions": {"type": "array", "items": {"type": "object", "required": ["key", "label"], "properties": {
            "key": {"type": "string", "description": "snake_case"}, "label": {"type": "string"}}}},
        "intro": {"type": "object", "required": ["title", "body", "learning_objective"], "properties": {
            "title": {"type": "string"}, "body": {"type": "string"}, "learning_objective": {"type": "string"},
            "image_prompt": {"type": "string"}, "image_alt": {"type": "string"}}},
        "featured_slide": {"type": "object", "required": ["title", "body", "character_title", "point1", "point2"], "properties": {
            "title": {"type": "string"}, "body": {"type": "string"},
            "character_title": {"type": "string", "description": "The featured character's role, e.g. 'Parent of a 7th grader'"},
            "point1": {"type": "string"}, "point2": {"type": "string"}}},
        "cast_note": {"type": "string", "description": "One or two sentences on who the featured character is."},
        "decisions": {"type": "array", "items": _DECISION},
        "summary": _SUMMARY,
        "review_notes": {"type": "array", "items": {"type": "string"},
                         "description": "Anything a human reviewer should check (facts, policy, sensitivity)."}}},
}

TRACKS_TOOL = {
    "name": "submit_tracks",
    "description": "Submit the slides for every track of one decision point.",
    "input_schema": {"type": "object", "required": ["tracks"], "properties": {
        "tracks": {"type": "array", "items": {"type": "object", "required": ["slides"], "properties": {
            "slides": {"type": "array", "items": _SLIDE}}}}}},
}

EXTEND_TOOL = {
    "name": "submit_extension",
    "description": "Submit the new decision points that continue the simulation.",
    "input_schema": {"type": "object", "required": ["decisions"], "properties": {
        "decisions": {"type": "array", "items": _DECISION},
        "summary": _SUMMARY,
        "review_notes": {"type": "array", "items": {"type": "string"}}}},
}


def generator_ready() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def call_claude(prompt: str, tool: dict = TOOL) -> dict:
    import anthropic  # lazy

    client = anthropic.Anthropic()
    # Streamed: the SDK refuses long non-streaming requests, and big sims are long.
    with client.messages.stream(
        model=DEFAULT_MODEL,
        max_tokens=32000,
        system=SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()
    for part in msg.content:
        if getattr(part, "type", "") == "tool_use":
            return part.input
    raise RuntimeError("The model did not return a simulation.")


def _structure_lines(split: list[int], options: int, slides: int, start: int = 1) -> str:
    lines = []
    for i, t in enumerate(split):
        lines.append(f"- Decision {start + i}: exactly {options} options and exactly {t} track(s) "
                     f"(give one track_summaries line per track). Every track must be used by at least one option.")
    return "\n".join(lines)


def build_prompt(inp: dict) -> str:
    featured = inp.get("featured") or {}
    others = [c for c in inp.get("characters") or [] if (c.get("name") or "").strip()]
    return "\n".join(filter(None, [
        f"Simulation title: {inp['title']}",
        f"Audience (the learner's role): {inp.get('audience') or 'school leaders'}",
        f"Tone: {inp.get('tone') or 'realistic and challenging'}",
        f"Situation / background:\n{inp['context']}",
        f"Learning objective (if given): {inp['objective']}" if inp.get("objective") else "",
        f"Featured character role: {featured.get('role') or 'choose the most important other person in the situation'}",
        (f"The featured character's name is fixed: {featured['name']}. Write it as [[FEATURED]]."
         if featured.get("name") else ""),
        ("Other characters (use these names): " + "; ".join(f"{c['name']} ({c.get('role', '')})" for c in others)
         if others else ""),
        f"Setting / region: {inp['setting']}" if inp.get("setting") else "",
        "",
        "Structure (match exactly):",
        _structure_lines(inp["tracks_split"], inp["options_per_decision"], inp["slides_per_track"]),
        "- Exactly 3 impact dimensions that matter for this situation (e.g. Trust, Student wellbeing, Policy risk).",
        "- Each option: pros and cons (1-2 each)." if inp.get("pros_cons", True) else "- Leave pros and cons empty.",
        "- Summary: debrief, one reflection question, 2-3 takeaways." if inp.get("summary", True)
        else "- Summary: keep it to a short closing and one reflection question.",
        f"\nExtra guidance: {inp['notes']}" if inp.get("notes") else "",
    ]))


# ---------------------------------------------------------------------------
# Assembly: model output -> nodes
# ---------------------------------------------------------------------------

def _clean(s: Any) -> str:
    return str(s or "").strip()


def _fix_name(value: Any, name: str) -> Any:
    if isinstance(value, str):
        return value.replace(FEATURED_TOKEN, name)
    if isinstance(value, list):
        return [_fix_name(v, name) for v in value]
    if isinstance(value, dict):
        return {k: _fix_name(v, name) for k, v in value.items()}
    return value


def _key(s: Any) -> str:
    return re.sub(r"[^a-z0-9_]", "_", _clean(s).lower())


def _keyboard_hint(n: int) -> str:
    keys = [f"[{LETTERS[i]}]" for i in range(n)]
    return "Press " + ", ".join(keys[:-1]) + f" or {keys[-1]} to decide"


def build_decisions(decisions: list[dict], dims: dict[str, str], featured: dict, *, first_number: int,
                    next_after_last: str, pros_cons: bool, used_ids: set[str]) -> tuple[dict, list[str], list[str]]:
    """Turn the model's decisions into wired nodes. Returns (nodes, problems, ids of each decision)."""
    nodes: dict[str, dict] = {}
    problems: list[str] = []

    def uid(base: str) -> str:
        i, cand = 1, base
        while cand in used_ids or cand in nodes:
            i += 1
            cand = f"{base}_{i}"
        used_ids.add(cand)
        return cand

    norm_keys = {_key(k): k for k in dims}
    dec_ids = [uid(f"d{first_number + i}") for i in range(len(decisions))]
    for i, dec in enumerate(decisions):
        num = first_number + i
        next_id = dec_ids[i + 1] if i + 1 < len(dec_ids) else next_after_last
        tracks = dec.get("tracks") or []
        track_first: list[str] = []
        for t_idx, track in enumerate(tracks):
            slides = track.get("slides") or []
            if not slides:
                problems.append(f"Decision {num}, track {t_idx + 1} has no slides.")
                slides = [{"kind": "outcome", "title": "", "body": ""}]
            ids = [uid(f"d{num}t{t_idx + 1}s{s + 1}") for s in range(len(slides))]
            track_first.append(ids[0])
            for s, slide in enumerate(slides):
                kind = "outcome" if s == 0 else (slide.get("kind") if slide.get("kind") in ("scene", "character") else "scene")
                badge = f"Decision {num} · Track {t_idx + 1}"
                nxt = ids[s + 1] if s + 1 < len(ids) else next_id
                if kind == "outcome":
                    node = {"type": "outcome", "next_node_id": nxt, "step_badge": badge, "you_chose": "",
                            "title": _clean(slide.get("title")), "body": _clean(slide.get("body")), "impacts": []}
                elif kind == "character":
                    node = {"type": "text_image", "next_node_id": nxt, "step_badge": badge,
                            "title": _clean(slide.get("title")), "body": _clean(slide.get("body")),
                            "character_name": featured["name"], "character_title": featured["title"],
                            "character_alt": featured["alt"],
                            "ti_point1": _clean(slide.get("point1")) or _clean(slide.get("title")),
                            "ti_point2": _clean(slide.get("point2")) or "—"}
                else:
                    node = {"type": "context", "next_node_id": nxt, "step_badge": badge,
                            "title": _clean(slide.get("title")), "body": _clean(slide.get("body"))}
                if kind != "character" and _clean(slide.get("image_prompt")):
                    node["image_prompt"] = _clean(slide["image_prompt"])
                    node["image_alt"] = _clean(slide.get("image_alt"))
                nodes[ids[s]] = node

        opts = dec.get("options") or []
        used_tracks: set[int] = set()
        choices = []
        for o_idx, opt in enumerate(opts[:len(LETTERS)]):
            t = opt.get("track")
            if not isinstance(t, int) or not 1 <= t <= len(track_first):
                t = (o_idx % max(1, len(track_first))) + 1
            used_tracks.add(t)
            impacts = []
            for imp in opt.get("impacts") or []:
                key = _key(imp.get("key"))
                key = norm_keys.get(key, key)
                if key in dims and imp.get("direction") in ("up", "down", "mid"):
                    impacts.append({"key": key, "label": dims[key], "direction": imp["direction"],
                                    "body": _clean(imp.get("body"))})
            choice = {"id": LETTERS[o_idx], "label": _clean(opt.get("label")),
                      "target_node_id": track_first[t - 1] if track_first else next_id, "impacts": impacts}
            if pros_cons:
                choice["pros"] = [_clean(p) for p in opt.get("pros") or [] if _clean(p)]
                choice["cons"] = [_clean(c) for c in opt.get("cons") or [] if _clean(c)]
            choices.append(choice)
        for t in range(1, len(track_first) + 1):
            if t not in used_tracks:
                problems.append(f"Decision {num}: no option leads to track {t}.")
        title = _clean(dec.get("short_title"))
        nodes[dec_ids[i]] = {
            "type": "decision", "next_node_id": None,
            "step_badge": f"Decision {num}" + (f": {title}" if title else ""),
            "prompt": _clean(dec.get("prompt")), "keyboard_hint": _keyboard_hint(len(choices)),
            "choices": choices,
        }
    return nodes, problems, dec_ids


def featured_info(inp: dict, character_title: str) -> dict:
    fixed = _clean((inp.get("featured") or {}).get("name"))
    name = fixed or PLACEHOLDER_NAME
    return {"name": name, "fixed": bool(fixed), "title": character_title,
            "alt": f"Portrait of {name}, {character_title}"}


def assemble(inp: dict, out: dict, app_ui_en: dict) -> dict:
    """Model output + inputs -> {scenario, meta, problems}."""
    problems: list[str] = []
    dims = {}
    for d in out.get("impact_dimensions") or []:
        key = re.sub(r"[^a-z0-9_]", "_", _clean(d.get("key")).lower()) or f"dim{len(dims) + 1}"
        dims[key] = _clean(d.get("label")) or key
        d["key"] = key
    # The model's impact keys may not be normalised the same way; map loosely.
    norm = {re.sub(r"[^a-z0-9_]", "_", k.lower()): k for k in dims}
    for dec in out.get("decisions") or []:
        for opt in dec.get("options") or []:
            for imp in opt.get("impacts") or []:
                k = re.sub(r"[^a-z0-9_]", "_", _clean(imp.get("key")).lower())
                imp["key"] = norm.get(k, k)

    fs = out.get("featured_slide") or {}
    featured = featured_info(inp, _clean(fs.get("character_title")) or _clean((inp.get("featured") or {}).get("role")))
    out = _fix_name(out, featured["name"])
    fs = out.get("featured_slide") or {}
    intro = out.get("intro") or {}
    summ = out.get("summary") or {}

    nodes: dict[str, dict] = {
        "intro": {"type": "context", "next_node_id": "intro_char", "step_badge": "Context",
                  "title": _clean(intro.get("title")), "body": _clean(intro.get("body")),
                  "learning_objective_label": "Learning Objective",
                  "learning_objective_body": _clean(intro.get("learning_objective"))},
        "intro_char": {"type": "text_image", "next_node_id": "d1", "step_badge": "The situation",
                       "title": _clean(fs.get("title")), "body": _clean(fs.get("body")),
                       "character_name": featured["name"], "character_title": featured["title"],
                       "character_alt": featured["alt"],
                       "ti_point1": _clean(fs.get("point1")), "ti_point2": _clean(fs.get("point2"))},
    }
    if _clean(intro.get("image_prompt")):
        nodes["intro"]["image_prompt"] = _clean(intro["image_prompt"])
        nodes["intro"]["image_alt"] = _clean(intro.get("image_alt"))

    decs = out.get("decisions") or []
    dnodes, dprob, dec_ids = build_decisions(decs, dims, featured, first_number=1, next_after_last="summary",
                                             pros_cons=inp.get("pros_cons", True), used_ids=set(nodes) | {"summary"})
    problems += dprob
    if dec_ids:
        nodes["intro_char"]["next_node_id"] = dec_ids[0]
    nodes.update(dnodes)
    nodes["summary"] = summary_node(summ, inp.get("summary", True))

    # Counts requested vs. returned.
    if len(decs) != inp["decisions"]:
        problems.append(f"Asked for {inp['decisions']} decisions, got {len(decs)}.")
    for i, dec in enumerate(decs[:inp["decisions"]]):
        if len(dec.get("options") or []) != inp["options_per_decision"]:
            problems.append(f"Decision {i + 1}: asked for {inp['options_per_decision']} options, "
                            f"got {len(dec.get('options') or [])}.")
        want_t = inp["tracks_split"][i]
        if len(dec.get("tracks") or []) != want_t:
            problems.append(f"Decision {i + 1}: asked for {want_t} track(s), got {len(dec.get('tracks') or [])}.")
    if len(dims) < 2:
        problems.append("Fewer than 2 impact dimensions; scores will be thin.")

    title = _clean(out.get("title")) or inp["title"]
    en = {"nodes": nodes, "ui": sim_ui(app_ui_en, title=title, audience=inp.get("audience", ""),
                                       scenario=_clean(out.get("context_summary")),
                                       objective=_clean(intro.get("learning_objective")),
                                       cast=_clean(out.get("cast_note")))}
    problems += check_graph(nodes, "intro")
    return {
        "scenario": {"start_node_id": "intro", "en": en},
        "title": title,
        "context_summary": _clean(out.get("context_summary")),
        "featured": featured,
        "impact_dimensions": [{"key": k, "label": v} for k, v in dims.items()],
        "review_notes": [str(n) for n in out.get("review_notes") or []],
        "problems": problems,
    }


def summary_node(summ: dict, full: bool) -> dict:
    node = {"type": "summary", "next_node_id": None, "step_badge": "Summary",
            "title": _clean(summ.get("title")) or "Reflect on your decisions",
            "reflection_prompt_label": "Reflection Prompt",
            "question": _clean(summ.get("question")) or "What would you do differently next time?"}
    if full and _clean(summ.get("body")):
        node["body"] = _clean(summ["body"])
    takeaways = [_clean(t) for t in summ.get("takeaways") or [] if _clean(t)]
    if full and takeaways:
        node["takeaways_label"] = "Key takeaways"
        node["takeaways"] = takeaways
    return node


def sim_ui(app_ui: dict, *, title: str, audience: str, scenario: str, objective: str, cast: str) -> dict:
    """The app's interface strings, with this sim's own title and brief."""
    ui = copy.deepcopy(app_ui)
    ui["sim_title"] = title
    if audience:
        ui["setup_subtitle"] = f"For {audience}"
    if scenario:
        ui["brief_scenario_body"] = scenario
    if objective:
        ui["brief_objective_body"] = objective
    if cast:
        ui["cast_panel_body"] = cast
    return ui


def write_tracks(story: str, decisions: list[dict], inp: dict, first_number: int, after_last: str,
                 call: Callable[..., dict]) -> list[str]:
    """Second pass: write every decision's track slides, all decisions at the same time.
    Fills dec["tracks"] in place; returns problems."""
    from concurrent.futures import ThreadPoolExecutor

    slides = inp["slides_per_track"]

    def one(i: int) -> tuple[int, list | None, str]:
        dec = decisions[i]
        summaries = dec.get("track_summaries") or []
        n_tracks = max(1, len(summaries))
        nxt = (f"the next decision point: \"{decisions[i + 1].get('prompt', '')}\""
               if i + 1 < len(decisions) else after_last)
        opts = "\n".join(f"  {LETTERS[k]}. {o.get('label', '')} -> track {o.get('track')}"
                          for k, o in enumerate(dec.get("options") or []))
        tracks = "\n".join(f"  Track {t + 1}: {s}" for t, s in enumerate(summaries)) or "  Track 1"
        prompt = "\n".join([
            story,
            "",
            f"Write the tracks for decision point {first_number + i}: {dec.get('prompt', '')}",
            "Options and the track each leads to:", opts,
            "Tracks to write:", tracks,
            f"After each track the learner goes to {nxt}. End each track so that this follows naturally.",
            f"Write exactly {n_tracks} track(s), each with exactly {slides} slide(s). The first slide of every "
            "track is kind 'outcome' (the immediate consequence of the choice); any later slides are 'scene' or "
            "'character' and show how the situation develops.",
        ])
        last_err = ""
        for _ in range(2):  # one retry
            try:
                out = call(prompt, TRACKS_TOOL)
                got = out.get("tracks") if isinstance(out, dict) else None
                if got:
                    return i, got, ""
                last_err = "no tracks returned"
            except Exception as e:  # keep the rest of the sim
                last_err = str(e)[:200]
        return i, None, last_err

    problems = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(decisions)))) as pool:
        for i, got, err in pool.map(one, range(len(decisions))):
            if got is None:
                decisions[i]["tracks"] = [{"slides": []} for _ in decisions[i].get("track_summaries") or [1]]
                problems.append(f"Decision {first_number + i}: couldn't write its tracks ({err}). Regenerate the sim.")
            else:
                decisions[i]["tracks"] = got
    return problems


def story_brief(inp: dict, out: dict) -> str:
    """Context every track-writing call gets."""
    dims = ", ".join(f"{d.get('key')} ({d.get('label')})" for d in out.get("impact_dimensions") or [])
    others = "; ".join(f"{c['name']} ({c.get('role', '')})" for c in inp.get("characters") or [] if c.get("name"))
    return "\n".join(filter(None, [
        f"Simulation: {out.get('title') or inp['title']}",
        f"Audience: {inp.get('audience') or 'school leaders'}; tone: {inp.get('tone') or 'realistic'}",
        f"Background: {inp['context']}",
        f"Setting: {inp['setting']}" if inp.get("setting") else "",
        f"Featured character (write their name as [[FEATURED]]): {(out.get('featured_slide') or {}).get('character_title', '')}",
        f"Other characters: {others}" if others else "",
        f"Impact dimensions: {dims}" if dims else "",
        "Decision points in order:",
        *[f"  {k + 1}. {d.get('prompt', '')}" for k, d in enumerate(out.get("decisions") or [])],
    ]))


def generate(inp: dict, app_ui_en: dict, model_call: Callable[..., dict] | None = None) -> dict:
    """Two passes so big sims stay fast: (1) outline - intro, decisions, options, scores,
    one line per track, summary; (2) every decision's track slides, in parallel."""
    inp = check_inputs(inp)
    call = model_call or call_claude
    out = call(build_prompt(inp), TOOL)
    if not isinstance(out, dict):
        raise RuntimeError("The model returned nothing usable.")
    track_problems = write_tracks(story_brief(inp, out), out.get("decisions") or [], inp, 1, "the summary", call)
    result = assemble(inp, out, app_ui_en)
    result["problems"] = track_problems + result["problems"]
    result["inputs"] = inp
    result["model"] = DEFAULT_MODEL
    return result


# ---------------------------------------------------------------------------
# Extend an existing sim
# ---------------------------------------------------------------------------

def story_so_far(nodes: dict, start: str) -> str:
    """Readable outline of a sim for the extension prompt."""
    lines, seen, queue = [], set(), [start]
    while queue:
        nid = queue.pop(0)
        if nid in seen or nid not in nodes:
            continue
        seen.add(nid)
        n = nodes[nid]
        t = n.get("type")
        if t == "decision":
            lines.append(f"DECISION ({nid}): {n.get('prompt', '')}")
            for c in n.get("choices", []):
                lines.append(f"  {c['id']}. {c.get('label', '')} -> {c.get('target_node_id')}")
                queue.append(c.get("target_node_id"))
        elif t != "summary":
            lines.append(f"[{nid}] {n.get('title', '') or n.get('quote', '')}: {n.get('body', '')}")
            queue.append(n.get("next_node_id"))
    return "\n".join(lines)


def extend(scenario: dict, ext: dict, model_call: Callable[..., dict] | None = None) -> dict:
    """Append new decision points before the summary of `scenario` (English master).
    Returns {scenario, problems, review_notes}; other languages are dropped and must be redone."""
    en = copy.deepcopy(scenario["en"])
    nodes, start = en["nodes"], scenario["start_node_id"]
    summary_id = next((k for k, v in nodes.items() if v.get("type") == "summary"), None)
    if not summary_id:
        raise ValueError("This sim has no summary slide to extend before.")
    ends = [k for k, v in nodes.items() if v.get("next_node_id") == summary_id]
    if not ends:
        raise ValueError("Couldn't find where this sim's tracks end.")

    inp = check_inputs({"title": "x", "context": "x", **ext})
    # Impact dimensions already used in the sim (so scores stay comparable).
    dims: dict[str, str] = {}
    for n in nodes.values():
        for imp in n.get("impacts", []) + [i for c in n.get("choices", []) for i in c.get("impacts", [])]:
            dims.setdefault(imp["key"], imp["label"])
    featured_node = next((n for n in nodes.values() if n.get("type") == "text_image"), {})
    featured = {"name": featured_node.get("character_name", PLACEHOLDER_NAME),
                "title": featured_node.get("character_title", ""),
                "alt": featured_node.get("character_alt", "")}
    n_existing = sum(1 for n in nodes.values() if n.get("type") == "decision")

    prompt = "\n".join(filter(None, [
        f"Simulation: {en['ui'].get('sim_title', '')}",
        f"Background: {en['ui'].get('brief_scenario_body', '')}",
        f"Featured character: write their name as [[FEATURED]] ({featured['title']}).",
        "The simulation so far (every track currently flows to the summary):",
        story_so_far(nodes, start),
        "",
        f"Continue the story with {inp['decisions']} NEW decision point(s) that follow on naturally, whichever track the learner saw.",
        "Structure (match exactly):",
        _structure_lines(inp["tracks_split"], inp["options_per_decision"], inp["slides_per_track"], n_existing + 1),
        ("Use exactly these impact dimension keys: " + ", ".join(f"{k} ({v})" for k, v in dims.items()))
        if dims else "Use 3 impact dimensions that fit the situation.",
        "- Each option: pros and cons (1-2 each)." if inp.get("pros_cons", True) else "- Leave pros and cons empty.",
        "Also return an updated summary that covers the whole simulation." if inp.get("summary", True) else "",
        f"Extra guidance: {ext['notes']}" if ext.get("notes") else "",
    ]))
    call = model_call or call_claude
    out = call(prompt, EXTEND_TOOL)
    if not isinstance(out, dict):
        raise RuntimeError("The model returned nothing usable.")
    story = "\n".join([f"Simulation: {en['ui'].get('sim_title', '')}",
                       f"Background: {en['ui'].get('brief_scenario_body', '')}",
                       f"Featured character (write their name as [[FEATURED]]): {featured['title']}",
                       "The story so far:", story_so_far(nodes, start)])
    track_problems = write_tracks(story, out.get("decisions") or [], inp, n_existing + 1, "the closing summary", call)
    if not dims:  # sim had no scores yet; take what the model used
        for dec in out.get("decisions") or []:
            for opt in dec.get("options") or []:
                for imp in opt.get("impacts") or []:
                    dims.setdefault(imp.get("key", ""), imp.get("key", "").replace("_", " ").title())
    out = _fix_name(out, featured["name"])
    new_nodes, problems, dec_ids = build_decisions(
        out.get("decisions") or [], dims, featured, first_number=n_existing + 1, next_after_last=summary_id,
        pros_cons=inp.get("pros_cons", True), used_ids=set(nodes))
    if not dec_ids:
        raise RuntimeError("The model returned no new decisions.")
    for k in ends:
        nodes[k]["next_node_id"] = dec_ids[0]
    nodes.update(new_nodes)
    if out.get("summary") and inp.get("summary", True):
        old = nodes[summary_id]
        new = summary_node(out["summary"], True)
        new["step_badge"] = old.get("step_badge", new["step_badge"])
        nodes[summary_id] = new
    problems = track_problems + problems + check_graph(nodes, start)
    return {"scenario": {"start_node_id": start, "en": en}, "problems": problems,
            "review_notes": [str(n) for n in out.get("review_notes") or []], "inputs": inp}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_graph(nodes: dict, start: str) -> list[str]:
    """Every link points somewhere, every slide is reachable, every path ends at a summary, no loops."""
    problems = []
    if start not in nodes:
        return [f"Start slide '{start}' is missing."]

    def targets(n: dict) -> list[str]:
        if n.get("type") == "decision":
            return [c.get("target_node_id") for c in n.get("choices", [])]
        return [n["next_node_id"]] if n.get("next_node_id") else []

    for nid, n in nodes.items():
        for t in targets(n):
            if t not in nodes:
                problems.append(f"{nid} points to a missing slide '{t}'.")
        if n.get("type") == "decision" and len(n.get("choices", [])) < 2:
            problems.append(f"{nid} has fewer than 2 options.")
        if n.get("type") not in ("decision", "summary") and not n.get("next_node_id"):
            problems.append(f"{nid} is a dead end (no next slide).")
    if problems:
        return problems
    # DFS for reachability + cycles
    state: dict[str, int] = {}

    def visit(nid: str) -> None:
        state[nid] = 1
        for t in targets(nodes[nid]):
            if state.get(t) == 1:
                problems.append(f"Loop through {t}.")
            elif t not in state:
                visit(t)
        state[nid] = 2

    visit(start)
    unreachable = [k for k in nodes if k not in state]
    if unreachable:
        problems.append("Unreachable slides: " + ", ".join(unreachable[:6]))
    return problems


TEXT_KEYS = ("title", "body", "prompt", "question", "quote")


def narration_text(node: dict) -> str:
    """What is read aloud for a slide (the same words are on screen, which doubles as the transcript)."""
    t = node.get("type")
    if t == "decision":
        return ". ".join([node.get("prompt", "")] + [f"Option {c['id']}: {c['label']}" for c in node.get("choices", [])])
    if t == "context":
        return " ".join(p for p in [node.get("title"), node.get("body"), node.get("learning_objective_body")] if p)
    if t == "summary":
        return " ".join(p for p in [node.get("title"), node.get("body"), node.get("question")] if p)
    if t == "video":
        return node.get("quote", "")
    return " ".join(p for p in [node.get("title"), node.get("body")] if p)


def ada_check(scenario: dict, tts_ready: bool) -> dict:
    """Accessibility checks for every language of a sim."""
    issues = []
    for code, block in scenario.items():
        if not isinstance(block, dict) or "nodes" not in block:
            continue
        for nid, n in block["nodes"].items():
            if not narration_text(n).strip():
                issues.append(f"[{code}] {nid}: no narration text.")
            if n.get("image_prompt") and not (n.get("image_alt") or "").strip():
                issues.append(f"[{code}] {nid}: image has no alt text.")
            if n.get("type") == "text_image" and not (n.get("character_alt") or "").strip():
                issues.append(f"[{code}] {nid}: character portrait has no alt text.")
            if n.get("type") == "video" and not n.get("quote"):
                issues.append(f"[{code}] {nid}: video has no caption script.")
    return {
        "passed": not issues,
        "issues": issues,
        "notes": [
            "Narration: every slide is read aloud from its on-screen text"
            + (" (natural voice)." if tts_ready else " (browser voice; set ELEVENLABS_API_KEY for a natural voice)."),
            "Captions/transcript: narration reads exactly the on-screen text; videos get WebVTT captions when added.",
            "Images: every illustration has alt text; character portraits are described by name and role.",
        ],
    }


# ---------------------------------------------------------------------------
# Translate a generated sim
# ---------------------------------------------------------------------------

STORY_UI = ("sim_title", "setup_subtitle", "brief_scenario_body", "brief_objective_body", "cast_panel_body")


def translate(scenario: dict, code: str, language_name: str, setting: str, notes: str, app_ui: dict | None,
              model_call: Callable[[str], dict] | None = None) -> dict:
    """Localize a sim's English master into `code`. If the app already has its interface
    strings for that language (app_ui), only the story is sent to the model."""
    if not localize.CODE_RE.match(code):
        raise ValueError("Language code must look like 'ne', 'es' or 'es-MX'.")
    en = scenario["en"]
    # Image prompts stay in English (they go to the image model); names are swapped per language.
    en_flat = {p: t for p, t in localize.flatten(en).items()
               if not p.endswith((".image_prompt", ".image_url", ".character_name"))}
    if app_ui:
        wanted = {p: t for p, t in en_flat.items()
                  if p.startswith("nodes.") or p.split(".")[1] in STORY_UI}
    else:
        wanted = en_flat
    rows = [{"path": p, "english": t} for p, t in wanted.items()]
    head = (f"Target language: {language_name or code} (code: {code})\n"
            f"Target setting: {setting or 'a typical school in a region where this language is spoken'}\n"
            + (f"Extra guidance from the reviewer: {notes}\n" if notes else ""))
    # Big sims are translated in chunks at the same time (keeps each request short).
    chunks = [rows[i:i + 70] for i in range(0, len(rows), 70)] or [[]]
    call = model_call or (lambda p: localize.call_claude(p))

    def one(chunk: list) -> dict:
        for attempt in range(2):
            try:
                r = call(head + "\nFields to write (JSON):\n" + json.dumps(chunk, ensure_ascii=False, indent=1))
                return r if isinstance(r, dict) else {}
            except Exception:
                if attempt:
                    return {}
        return {}

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(6, len(chunks))) as pool:
        parts = list(pool.map(one, chunks))
    strings: dict = {}
    result = {"review_notes": []}
    for part in parts:
        strings.update(part.get("strings", {}) or {})
        result["review_notes"] += part.get("review_notes", []) or []
    block = copy.deepcopy(en)
    if app_ui:
        story = {k: en["ui"][k] for k in STORY_UI if k in en["ui"]}
        block["ui"] = {**copy.deepcopy(app_ui), **story}
    else:
        block["ui"]["speech_locale"] = code
    back, missing = {}, []
    for p in wanted:
        item = strings.get(p)
        if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip():
            localize.set_path(block, p, item["text"])
            back[p] = item.get("back_translation", "")
        else:
            missing.append(p)
    if language_name:
        block["ui"].setdefault("language_labels", {})[code] = language_name
    problems = [f"Model skipped: {p}" for p in missing]
    for p, t in en_flat.items():
        if PLACEHOLDER_NAME in t and p in wanted and PLACEHOLDER_NAME not in localize.flatten(block).get(p, ""):
            problems.append(f"{p} lost the character name placeholder.")
    return {"block": block, "back_translation": back, "problems": problems,
            "review_notes": [str(n) for n in result.get("review_notes", [])] if isinstance(result, dict) else []}
