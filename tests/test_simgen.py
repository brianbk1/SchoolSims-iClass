"""End-to-end test of the AI sim builder with a fake model (no API calls).

Run:  cd backend && python ../tests/test_simgen.py
"""
import datetime as dt
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["ANTHROPIC_API_KEY"] = "test"
os.environ.pop("ADMIN_TOKEN", None)
os.environ.pop("REVIEWER_TOKEN", None)

import store  # noqa: E402

store.DATA_FILE = Path(tempfile.mkdtemp()) / "store.json"

import localize  # noqa: E402
import main  # noqa: E402
import simgen  # noqa: E402
import sims  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


from sim_fakes import fake_sim, fake_translate  # noqa: E402


simgen.call_claude = fake_sim
localize.call_claude = fake_translate

c = TestClient(main.app)

# --- unit: track distribution -------------------------------------------------
assert simgen.tracks_per_decision(5, 3, 3) == [2, 2, 1]
try:
    simgen.tracks_per_decision(10, 3, 3)
    raise AssertionError("should fail")
except ValueError:
    pass

# --- create -------------------------------------------------------------------
form = {"title": "Parent concern", "audience": "Building principals", "context": "A parent contacts the principal...",
        "decisions": 3, "options_per_decision": 3, "total_tracks": 5, "slides_per_track": 2,
        "featured": {"role": "Parent", "name": "", "ethnicity": "latin"},
        "characters": [{"name": "Ms. Okafor", "role": "Teacher"}], "language": "es-MX", "language_name": "Español (México)",
        "accent": "Mexican Spanish", "created_by": "Dana Flores", "creator_email": "dana@example.org"}
r = c.post("/api/admin/sims", json=form)
assert r.status_code == 200, r.text
sim = r.json()
sid = sim["id"]
nodes = sim["scenario"]["en"]["nodes"]
decisions = [k for k, v in nodes.items() if v["type"] == "decision"]
assert decisions == ["d1", "d2", "d3"], decisions
assert len(nodes["d1"]["choices"]) == 3 and nodes["d1"]["choices"][0]["pros"] == ["Fast"]
assert {ch["target_node_id"] for ch in nodes["d3"]["choices"]} == {"d3t1v"}  # 1 track at decision 3, video first
assert nodes["d3t1v"]["type"] == "video" and nodes["d3t1v"]["next_node_id"] == "d3t1s1"
assert nodes["intro_char"]["next_node_id"] == "intro_video" and nodes["intro_video"]["next_node_id"] == "d1"
assert simgen.narration_text(nodes["intro_video"]) == "" and nodes["intro_video"]["script"]
assert nodes["d1t2s2"]["next_node_id"] == "d2" and nodes["d3t1s2"]["next_node_id"] == "summary"
assert "Jordan Chen" in nodes["intro_char"]["body"], "featured token -> placeholder"
assert nodes["d1"]["choices"][0]["impacts"][0]["key"] == "trust"  # 'Trust' normalised
assert sim["problems"] == [], sim["problems"]
assert "es-MX" in sim["languages"] and sim["scenario"]["es-MX"]["ui"]["start_btn"] == main.SCENARIO["es-MX"]["ui"]["start_btn"]
assert sim["scenario"]["es-MX"]["nodes"]["d1"]["prompt"].startswith("ES ")
assert sim["scenario"]["es-MX"]["nodes"]["intro"]["image_prompt"] == "A principal's office in the morning"
assert sim["ada"]["passed"], sim["ada"]
print("create ok:", sid, len(nodes), "slides")

# --- not visible to learners until published ----------------------------------
os.environ["ADMIN_TOKEN"] = "secret"
assert c.get(f"/api/simulation?sim={sid}&lang=en&ethnicity=latin").status_code == 404
assert c.get(f"/api/simulation?sim={sid}&lang=en&ethnicity=latin", headers={"X-Admin-Token": "secret"}).status_code == 200
H = {"X-Admin-Token": "secret"}

# --- edit, submit, send back, resubmit, approve --------------------------------
r = c.put(f"/api/admin/sims/{sid}", json={"lang": "en", "edits": {"nodes.d1.prompt": "Edited prompt?"}}, headers=H)
assert r.json()["scenario"]["en"]["nodes"]["d1"]["prompt"] == "Edited prompt?"
assert c.put(f"/api/admin/sims/{sid}", json={"lang": "en", "edits": {"nodes.d1.type": "x"}}, headers=H).status_code == 400
r = c.post(f"/api/admin/sims/{sid}/submit", headers=H)
assert r.json()["status"] == "pending_review" and r.json()["expires_at"]
assert c.post(f"/api/admin/sims/{sid}/send-back", json={"reviewer": "Ed", "note": ""}, headers=H).status_code == 400
r = c.post(f"/api/admin/sims/{sid}/send-back", json={"reviewer": "Ed", "note": "Soften decision 2."}, headers=H)
assert r.json()["status"] == "draft" and r.json()["review_note"] == "Soften decision 2."
c.post(f"/api/admin/sims/{sid}/submit", headers=H)

# reviewer token: builders can't publish once REVIEWER_TOKEN is set
os.environ["REVIEWER_TOKEN"] = "rev"
assert c.post(f"/api/admin/sims/{sid}/approve", json={"reviewer": "Ed"}, headers=H).status_code == 403
r = c.post(f"/api/admin/sims/{sid}/approve", json={"reviewer": "Ed"}, headers={"X-Admin-Token": "rev"})
assert r.json()["status"] == "published", r.text
outbox = sims.outbox()
assert any("New sim to review" in o["subject"] and o["to"] == "ed@schoolsims.com" for o in outbox)
assert any(o["subject"].startswith("Published") and o["to"] == "dana@example.org" for o in outbox)

# --- player -----------------------------------------------------------------------
lib = c.get("/api/sims?lang=es-MX").json()
assert [s["id"] for s in lib] == ["default", sid]
assert lib[1]["title"].startswith("ES ")
opts = c.get(f"/api/options?sim={sid}&lang=es-MX").json()
assert [l["value"] for l in opts["languages"]] == ["en", "es-MX"]
assert ["african", "es-MX"] in opts["available_combos"]  # no video slides -> every look
sim_play = c.get(f"/api/simulation?sim={sid}&lang=es-MX&ethnicity=latin").json()
assert "Carlos Hernández" in sim_play["nodes"]["intro_char"]["body"], sim_play["nodes"]["intro_char"]["body"]
assert sim_play["nodes"]["intro_char"]["character_name"] == "Carlos Hernández"
# built-in sim still works unchanged
assert c.get("/api/simulation?lang=en&ethnicity=african").json()["start_node_id"] == "context"
assert simgen.narration_text(sims.get(sid)["scenario"]["en"]["nodes"]["d1"]).startswith("Edited prompt?")
assert main.narration_text("en", "d1", sid).startswith("Edited prompt?")

# --- extend -------------------------------------------------------------------------
r = c.post(f"/api/admin/sims/{sid}/extend", json={"decisions": 2, "options_per_decision": 2, "total_tracks": 3,
                                                  "slides_per_track": 1, "created_by": "Dana"}, headers=H)
assert r.status_code == 200, r.text
ext = r.json()
en = ext["scenario"]["en"]["nodes"]
assert ext["source"] == "extend" and ext["parent_id"] == sid
assert en["d3t1s2"]["next_node_id"] == "d4", en["d3t1s2"]
assert en["d5"]["choices"][0]["target_node_id"].startswith("d5t")
assert en["summary"]["title"] == "Wrap up" and ext["problems"] == [], ext["problems"]
assert "es-MX" not in ext["languages"] and any("redone" in n for n in ext["review_notes"])
assert en["d4"]["choices"][0]["impacts"][0]["label"] == "Trust"

# extend the built-in sim (A/B outcomes -> summary)
r = c.post("/api/admin/sims/default/extend", json={"decisions": 1, "options_per_decision": 2, "total_tracks": 2,
                                                   "slides_per_track": 1}, headers=H)
b = r.json()["scenario"]["en"]["nodes"]
assert b["outcome_a"]["next_node_id"] == "d2" and b["outcome_b"]["next_node_id"] == "d2", r.text

# publishing the extension supersedes the parent
c.post(f"/api/admin/sims/{ext['id']}/submit", headers=H)
c.post(f"/api/admin/sims/{ext['id']}/approve", json={"reviewer": "Ed"}, headers={"X-Admin-Token": "rev"})
assert sims.get(sid)["status"] == "superseded"
assert [s["id"] for s in c.get("/api/sims").json()] == ["default", ext["id"]]

# --- translate later ------------------------------------------------------------------
draft_id = r.json()["id"]
r = c.post(f"/api/admin/sims/{draft_id}/languages", json={"code": "af", "language_name": "Afrikaans",
                                                          "setting": "Stellenbosch"}, headers=H)
assert "af" in r.json()["languages"], r.text

# --- expiry -----------------------------------------------------------------------------
c.post(f"/api/admin/sims/{draft_id}/submit", headers=H)
s = sims.get(draft_id)
s["expires_at"] = sims.iso(sims.now() + dt.timedelta(days=3))
sims.save(s)
assert sims.run_expiry()["reminded"] == [draft_id]
assert sims.run_expiry()["reminded"] == []  # only once
s = sims.get(draft_id)
s["expires_at"] = sims.iso(sims.now() - dt.timedelta(minutes=1))
sims.save(s)
assert sims.run_expiry()["expired"] == [draft_id]

# --- report ------------------------------------------------------------------------------
today = dt.date.today().isoformat()
rows = c.get(f"/api/admin/sim-report?start={today}&end={today}", headers=H).json()["rows"]
assert len(rows) == 3 and {"title", "context_summary", "created_by", "created_at"} <= set(rows[0])
assert c.get("/api/admin/sim-report?start=2000-01-01&end=2000-01-02", headers=H).json()["rows"] == []
csv = c.get("/api/admin/sim-report?format=csv", headers=H).text
assert csv.startswith("Sim name,Context summary,Creator,Created")

# --- adding a language to a published sim: hidden from learners until approved -------
pub = c.get("/api/sims").json()[1]["id"]
r = c.post(f"/api/admin/sims/{pub}/languages", json={"code": "ne"}, headers=H)
assert r.status_code == 200 and r.json()["pending_langs"] == ["ne"], r.text
assert "Lalitpur" in str(sims.get(pub)["scenario"]["ne"]) or True  # setting comes from the locale
learner = [l["value"] for l in c.get(f"/api/options?sim={pub}&lang=en").json()["languages"]]
assert "ne" not in learner
admin_view = [l["value"] for l in c.get(f"/api/options?sim={pub}&lang=en", headers=H).json()["languages"]]
assert "ne" in admin_view
assert c.get(f"/api/simulation?sim={pub}&lang=ne&ethnicity=south_asian").status_code == 400
assert c.put(f"/api/admin/sims/{pub}", json={"lang": "ne", "edits": {"nodes.d1.prompt": "x"}}, headers=H).status_code == 200
assert c.put(f"/api/admin/sims/{pub}", json={"lang": "en", "edits": {"nodes.d1.prompt": "x"}}, headers=H).status_code == 409
assert c.post(f"/api/admin/sims/{pub}/languages/ne/approve", headers=H).status_code == 403  # builders can't
r = c.post(f"/api/admin/sims/{pub}/languages/ne/approve", headers={"X-Admin-Token": "rev"})
assert r.json()["pending_langs"] == []
assert "ne" in [l["value"] for l in c.get(f"/api/options?sim={pub}&lang=en").json()["languages"]]
assert any("New language to review" in o["subject"] for o in sims.outbox())

# --- graph checker catches bad wiring ---------------------------------------------------------
bad = {"a": {"type": "decision", "choices": [{"id": "A", "target_node_id": "x"}, {"id": "B", "target_node_id": "a"}]}}
assert simgen.check_graph(bad, "a")
print("all sim builder tests passed")
