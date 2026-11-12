"""Sim library: the built-in sim (scenario.json) plus sims made with the AI builder.

Generated sims live in the runtime store (store.py: a local JSON file, or Upstash
Redis on Vercel) under the hash "sims", one record per sim:

    {id, title, status, builtin: false,
     created_by, creator_email, created_at, updated_at,
     submitted_at, expires_at, reminded_at, reviewed_by, reviewed_at, review_note,
     source: "ai" | "extend", parent_id,
     inputs: {...every builder field...},
     context_summary, review_notes: [...], problems: [...],
     default_look: persona id, accent,
     scenario: {start_node_id, en: {nodes, ui}, <locale>: {nodes, ui}, ...}}

Status flow:  draft -> pending_review -> published
                         |-> draft (sent back with a note)
                         |-> expired (30 days without review)
              published sims can be unpublished (-> draft) or superseded by an extension.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import secrets
import sys

import httpx

import store

HASH = "sims"
OUTBOX = "sim_outbox"
REVIEW_DAYS = int(os.getenv("SIM_REVIEW_DAYS", "30"))
REMIND_DAYS_BEFORE = 5
REVIEWER_EMAIL = os.getenv("SIM_REVIEWER_EMAIL", "ed@schoolsims.com")
BUILTIN_ID = "default"
STATUSES = ("draft", "pending_review", "published", "expired", "superseded")


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def parse(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def new_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "sim"
    return f"{slug}-{secrets.token_hex(3)}"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def all_generated() -> list[dict]:
    return list(store.hget_all(HASH).values())


def get(sim_id: str) -> dict | None:
    return store.hget(HASH, sim_id)


def save(sim: dict) -> dict:
    sim["updated_at"] = iso(now())
    store.hset(HASH, sim["id"], sim)
    return sim


def delete(sim_id: str) -> None:
    store.hdel(HASH, sim_id)
    for key in list(store.hget_all("sim_images")):
        if key.startswith(sim_id + "/"):
            store.hdel("sim_images", key)


def summary(sim: dict) -> dict:
    """List view of a sim (no content)."""
    langs = sorted(k for k, v in sim.get("scenario", {}).items() if isinstance(v, dict) and "nodes" in v)
    nodes = sim.get("scenario", {}).get("en", {}).get("nodes", {})
    return {
        "id": sim["id"], "title": sim.get("title", ""), "status": sim.get("status", "draft"),
        "builtin": sim.get("builtin", False), "source": sim.get("source", "ai"), "parent_id": sim.get("parent_id"),
        "created_by": sim.get("created_by", ""), "created_at": sim.get("created_at", ""),
        "submitted_at": sim.get("submitted_at", ""), "expires_at": sim.get("expires_at", ""),
        "reviewed_by": sim.get("reviewed_by", ""), "reviewed_at": sim.get("reviewed_at", ""),
        "review_note": sim.get("review_note", ""),
        "context_summary": sim.get("context_summary", ""), "languages": langs,
        "decisions": sum(1 for n in nodes.values() if n.get("type") == "decision"),
        "slides": len(nodes), "problems": len(sim.get("problems", [])),
    }


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

def submit(sim: dict, base_url: str = "") -> dict:
    t = now()
    sim.update(status="pending_review", submitted_at=iso(t),
               expires_at=iso(t + dt.timedelta(days=REVIEW_DAYS)), reminded_at="", review_note="")
    save(sim)
    notify(REVIEWER_EMAIL, f"New sim to review: {sim['title']}",
           f"{sim.get('created_by') or 'Someone'} submitted \"{sim['title']}\" for review.\n\n"
           f"{sim.get('context_summary', '')}\n\n"
           f"Review it in the admin: {base_url}/admin#sim={sim['id']}\n"
           f"It expires if not reviewed by {sim['expires_at'][:10]}.")
    return sim


def publish(sim: dict, reviewer: str) -> dict:
    sim.update(status="published", reviewed_by=reviewer, reviewed_at=iso(now()), review_note="")
    save(sim)
    parent = get(sim.get("parent_id") or "")
    if parent and parent.get("status") == "published" and sim.get("source") == "extend":
        parent["status"] = "superseded"
        save(parent)
    if sim.get("creator_email"):
        notify(sim["creator_email"], f"Published: {sim['title']}",
               f"\"{sim['title']}\" was approved by {reviewer or 'SchoolSims'} and is now in the library.")
    return sim


def send_back(sim: dict, reviewer: str, note: str) -> dict:
    sim.update(status="draft", reviewed_by=reviewer, reviewed_at=iso(now()), review_note=note,
               submitted_at="", expires_at="")
    save(sim)
    if sim.get("creator_email"):
        notify(sim["creator_email"], f"Changes requested: {sim['title']}",
               f"{reviewer or 'The reviewer'} sent \"{sim['title']}\" back:\n\n{note}")
    return sim


def unpublish(sim: dict) -> dict:
    sim["status"] = "draft"
    return save(sim)


def run_expiry(base_url: str = "") -> dict:
    """Remind the reviewer 5 days before a pending sim expires; expire it after 30 days.
    Run daily (Vercel cron -> /api/cron/sims) and whenever the admin list loads."""
    t = now()
    reminded, expired = [], []
    for sim in all_generated():
        if sim.get("status") != "pending_review":
            continue
        exp = parse(sim.get("expires_at", ""))
        if not exp:
            continue
        if t >= exp:
            sim["status"] = "expired"
            save(sim)
            expired.append(sim["id"])
            notify(REVIEWER_EMAIL, f"Expired without review: {sim['title']}",
                   f"\"{sim['title']}\" was pending for {REVIEW_DAYS} days and has expired. "
                   f"The creator can resubmit it from the admin.")
            if sim.get("creator_email"):
                notify(sim["creator_email"], f"Expired without review: {sim['title']}",
                       f"\"{sim['title']}\" wasn't reviewed within {REVIEW_DAYS} days. You can resubmit it.")
        elif t >= exp - dt.timedelta(days=REMIND_DAYS_BEFORE) and not sim.get("reminded_at"):
            sim["reminded_at"] = iso(t)
            save(sim)
            reminded.append(sim["id"])
            notify(REVIEWER_EMAIL, f"Reminder: {sim['title']} expires {exp.date()}",
                   f"\"{sim['title']}\" is still waiting for review: {base_url}/admin#sim={sim['id']}")
    return {"reminded": reminded, "expired": expired}


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def email_ready() -> bool:
    return bool(os.getenv("RESEND_API_KEY") and os.getenv("SIM_MAIL_FROM"))


def notify(to: str, subject: str, text: str) -> None:
    """Send with Resend when RESEND_API_KEY + SIM_MAIL_FROM are set; always keep a copy
    in the outbox (shown in the admin) so nothing is lost when email isn't set up."""
    entry = {"to": to, "subject": subject, "text": text, "at": iso(now()), "sent": False, "error": ""}
    if email_ready():
        try:
            r = httpx.post("https://api.resend.com/emails", timeout=10,
                           headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
                           json={"from": os.environ["SIM_MAIL_FROM"], "to": [to], "subject": subject, "text": text})
            r.raise_for_status()
            entry["sent"] = True
        except Exception as e:  # never block the workflow on email
            entry["error"] = str(e)[:200]
            print(f"Email to {to} failed: {e}", file=sys.stderr)
    print("SIM-NOTIFY " + subject, flush=True)
    try:
        store.lpush(OUTBOX, entry)
    except Exception as e:
        print(f"Outbox write failed: {e}", file=sys.stderr)


def outbox(count: int = 50) -> list[dict]:
    return store.lrange(OUTBOX, count)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(start: str = "", end: str = "") -> list[dict]:
    """Sims created between start and end (YYYY-MM-DD, inclusive), newest first."""
    s = parse(start + "T00:00:00+00:00") if start else None
    e = parse(end + "T23:59:59+00:00") if end else None
    rows = []
    for sim in all_generated():
        c = parse(sim.get("created_at", ""))
        if (s and (not c or c < s)) or (e and (not c or c > e)):
            continue
        rows.append({k: v for k, v in summary(sim).items()
                     if k in ("id", "title", "context_summary", "created_by", "created_at", "status",
                              "source", "languages", "decisions", "reviewed_by", "reviewed_at")})
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Images (optional: OpenAI image API, stored in the runtime store)
# ---------------------------------------------------------------------------

IMAGE_HASH = "sim_images"


def images_ready() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


IMAGE_STYLE = ("Photorealistic, natural light, softly composed editorial photo of a school or office setting. "
               "No text, no signage, no logos, no recognizable real people. ")


def generate_image(prompt: str) -> str:
    """Returns base64 JPEG."""
    r = httpx.post(
        "https://api.openai.com/v1/images/generations", timeout=120,
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={"model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"), "prompt": IMAGE_STYLE + prompt,
              "size": "1536x1024", "quality": os.getenv("OPENAI_IMAGE_QUALITY", "low"),
              "output_format": "jpeg", "output_compression": 70, "n": 1},
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Image API {r.status_code}: {r.text[:200]}")
    return r.json()["data"][0]["b64_json"]


def save_image(sim_id: str, node_id: str, b64: str) -> str:
    store.hset(IMAGE_HASH, f"{sim_id}/{node_id}", b64)
    return f"/api/sims/{sim_id}/images/{node_id}?v={secrets.token_hex(3)}"


def load_image(sim_id: str, node_id: str) -> str | None:
    return store.hget(IMAGE_HASH, f"{sim_id}/{node_id}")
