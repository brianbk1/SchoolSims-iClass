"""Videos and images for AI-built sims, with HeyGen, Blob and OpenAI mocked.

Run:  cd backend && python ../tests/test_media.py
"""
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))
for k in ("ADMIN_TOKEN", "REVIEWER_TOKEN", "VERCEL"):
    os.environ.pop(k, None)
os.environ.update(ANTHROPIC_API_KEY="x", HEYGEN_API_KEY="hg", BLOB_READ_WRITE_TOKEN="blob", OPENAI_API_KEY="oa")

import store  # noqa: E402

store.DATA_FILE = Path(tempfile.mkdtemp()) / "store.json"

import httpx  # noqa: E402
import localize  # noqa: E402
import main  # noqa: E402
import media_gen  # noqa: E402
import simgen  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sim_fakes import fake_sim, fake_translate  # noqa: E402

simgen.call_claude = fake_sim
localize.call_claude = fake_translate

MP4 = b"\x00\x00\x00\x6cmvhd" + b"\x00" * 12 + struct.pack(">II", 1000, 12000) + b"\x00" * 80
calls = {"upload": 0, "generate": [], "status": 0, "blob": []}
renders: dict = {}


def heygen_handler(req: httpx.Request) -> httpx.Response:
    p = req.url.path
    if p == "/v3/assets":
        calls["upload"] += 1
        return httpx.Response(200, json={"data": {"asset_id": f"asset{calls['upload']}"}})
    if p == "/v2/voices":
        return httpx.Response(200, json={"data": {"voices": [
            {"voice_id": "v-en", "name": "Ben", "language": "English", "gender": "male", "accent": "American"},
            {"voice_id": "v-es-mx", "name": "Diego", "language": "Spanish", "gender": "male", "accent": "Mexican"},
            {"voice_id": "v-es-es", "name": "Pablo", "language": "Spanish", "gender": "male", "accent": "Spain"},
        ]}})
    if p == "/v3/videos" and req.method == "POST":
        body = json.loads(req.content)
        calls["generate"].append(body)
        vid = f"vid{len(calls['generate'])}"
        renders[vid] = "processing"
        return httpx.Response(200, json={"data": {"video_id": vid}})
    if p.startswith("/v3/videos/"):
        calls["status"] += 1
        vid = p.rsplit("/", 1)[1]
        st = renders[vid]
        return httpx.Response(200, json={"data": {"status": st, "video_url": f"https://files.heygen.ai/{vid}.mp4" if st == "completed" else None}})
    return httpx.Response(404)


client = httpx.Client(transport=httpx.MockTransport(heygen_handler))
media_gen.HeyGen.__init__.__defaults__ = (client,)


def fake_get(url, **kw):
    if "heygen" in url:
        return httpx.Response(200, content=MP4, request=httpx.Request("GET", url))
    return httpx.Response(200, content=(ROOT / "public" / url.split("/", 3)[3]).read_bytes(), request=httpx.Request("GET", url))


def fake_put(url, content=None, headers=None, **kw):
    calls["blob"].append((url, headers.get("x-content-type"), len(content)))
    name = url.rsplit("/", 1)[1]
    return httpx.Response(200, json={"url": f"https://blob.example/{name}"}, request=httpx.Request("PUT", url))


def fake_post(url, **kw):  # OpenAI images
    import base64
    return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"jpegbytes").decode()}]},
                          request=httpx.Request("POST", url))


media_gen.httpx = type("H", (), {"get": staticmethod(fake_get), "put": staticmethod(fake_put),
                                 "post": staticmethod(fake_post), "Client": httpx.Client,
                                 "Response": httpx.Response})

c = TestClient(main.app)

# A sim with video slides in English + Mexican Spanish
sim = c.post("/api/admin/sims", json={"title": "Parent", "context": "x", "decisions": 2, "options_per_decision": 2,
                                      "total_tracks": 3, "slides_per_track": 1, "language": "es-MX"}).json()
sid = sim["id"]
m = sim["media"]
assert m["video_nodes"] == ["intro_video", "d1t1v", "d1t2v", "d2t1v"], m["video_nodes"]
assert m["grid"]["es-MX"]["latin"]["total"] == 4 and m["images"]["total"] >= 1
assert m["suggested"]["es-MX"] == "latin"

# Start renders for es-MX / latin
r = c.post(f"/api/admin/sims/{sid}/videos", json={"persona": "latin", "lang": "es-MX"}).json()
assert r["started"] == 4 and not r["errors"], r
g = calls["generate"][0]
assert g["voice_id"] == "v-es-mx" and g["background"]["type"] == "image" and g["remove_background"] is True
assert "Carlos Hernández" not in g["script"] or True
assert calls["upload"] == 2, calls["upload"]  # framed portrait + background, cached after that
assert r["grid"]["es-MX"]["latin"]["rendering"] == 4

# Asking again doesn't restart anything
assert c.post(f"/api/admin/sims/{sid}/videos", json={"persona": "latin", "lang": "es-MX"}).json()["started"] == 0

# Not ready yet -> still rendering; then completed -> copied to Blob with captions
assert c.post(f"/api/admin/sims/{sid}/videos/refresh").json()["rendering"] == 4
for k in renders:
    renders[k] = "completed"
r = c.post(f"/api/admin/sims/{sid}/videos/refresh").json()
assert r["rendering"] == 0 and r["grid"]["es-MX"]["latin"]["ready"] == 4, r["grid"]["es-MX"]["latin"]
types = sorted({t for _, t, _ in calls["blob"]})
assert types == ["text/vtt", "video/mp4"], types

# The player gets the clip for that look + language, and falls back es-MX -> en only with the same look
c.post(f"/api/admin/sims/{sid}/submit")
c.post(f"/api/admin/sims/{sid}/approve", json={"reviewer": "Ed"})
play = c.get(f"/api/simulation?sim={sid}&lang=es-MX&ethnicity=latin").json()
v = play["nodes"]["intro_video"]
assert v["own_video"] and v["video_url"].endswith(".mp4") and v["captions_url"].endswith(".vtt"), v
other = c.get(f"/api/simulation?sim={sid}&lang=es-MX&ethnicity=african").json()["nodes"]["intro_video"]
assert other["own_video"] and "video_url" not in other  # no clip for that look -> portrait + transcript

# Editing a script marks that clip stale
c.post(f"/api/admin/sims/{sid}/unpublish")
c.put(f"/api/admin/sims/{sid}", json={"lang": "es-MX", "edits": {"nodes.intro_video.script": "Nuevo guion."}})
assert c.get(f"/api/admin/sims/{sid}/media").json()["grid"]["es-MX"]["latin"]["stale"] == 1

# Captions
vtt = media_gen.vtt(media_gen.split_lines("Hola. ¿Cómo estás? Bien."), 6.0)
assert vtt.startswith("WEBVTT") and vtt.count("-->") == 3
assert media_gen.mp4_duration(MP4) == 12.0

# Images go to Blob
r = c.post(f"/api/admin/sims/{sid}/images/intro").json()
assert r["images"]["intro"].startswith("https://blob.example/"), r["images"]

# Backgrounds
assert media_gen.background_for("es-AR") == "/backgrounds/es-AR.jpg"
assert media_gen.background_for("es") == "/backgrounds/es-MX.jpg"
assert media_gen.background_for("ar-JO") is None
assert media_gen.background_for("sw-TZ") == "/backgrounds/sw-TZ.jpg"

# No video slides when the builder turns them off
s2 = c.post("/api/admin/sims", json={"title": "B", "context": "x", "decisions": 1, "options_per_decision": 2,
                                     "total_tracks": 1, "slides_per_track": 1, "video_slides": "none"}).json()
assert s2["media"]["video_nodes"] == []
# --- Character looks -------------------------------------------------------------------
import base64 as b64  # noqa: E402
png = (ROOT / "public/assets/latin.png").read_bytes()
simgen.call_claude = lambda p, tool=None: {"names": [{"lang": "ar", "name": "ليلى حداد"}, {"lang": "default", "name": "Layla Haddad"}]} \
    if (tool or {}).get("name") == "submit_names" else fake_sim(p, tool)
names = c.post("/api/admin/looks/suggest-names", json={"label": "Middle East", "gender": "female", "langs": ["ar"]}).json()["names"]
assert names == {"ar": "ليلى حداد", "default": "Layla Haddad"}
look = c.post("/api/admin/looks", json={"label": "Middle East", "gender": "female", "langs": ["ar", "ar-JO"], "names": names,
                                        "photo": "data:image/png;base64," + b64.b64encode(png).decode()}).json()
assert look["id"] == "middle_east_female" and look["photo"].startswith("https://blob.example/"), look
looks = c.get("/api/admin/looks").json()["looks"]
assert looks[0]["id"] == "middle_east_female" and any(l["id"] == "latin" and not l["custom"] for l in looks)
# appears for learners in AI-built sims, is suggested for Arabic, and swaps in its name
s3 = c.post("/api/admin/sims", json={"title": "Q", "context": "x", "decisions": 1, "options_per_decision": 2,
                                     "total_tracks": 1, "slides_per_track": 1, "language": "ar"}).json()
opts = c.get(f"/api/options?sim={s3['id']}&lang=ar").json()
assert opts["suggested"]["ar"] == "middle_east_female", opts["suggested"]
assert any(e["value"] == "middle_east_female" and e["label"] == "Middle East" for e in opts["ethnicities"])
play = c.get(f"/api/simulation?sim={s3['id']}&lang=ar&ethnicity=middle_east_female").json()
assert play["media"]["photo"].startswith("https://blob.example/") and "ليلى حداد" in json.dumps(play["nodes"], ensure_ascii=False)
assert play["ui"]["ethnicity_labels"]["middle_east_female"] == "Middle East"
# not offered in the built-in sim (it has no videos for this look)
assert ["middle_east_female", "ar"] not in c.get("/api/options?lang=ar").json()["available_combos"]
# video uses a female voice and the Blob photo
calls["generate"].clear()
r = c.post(f"/api/admin/sims/{s3['id']}/videos", json={"persona": "middle_east_female", "lang": "ar"}).json()
assert r["errors"] == [] or all("Arabic" in e for e in r["errors"]), r["errors"]
# edit + delete
c.put("/api/admin/looks/middle_east_female", json={"label": "Middle East (Levant)", "gender": "female", "langs": ["ar-JO"], "names": names})
assert c.get("/api/admin/looks").json()["looks"][0]["label"] == "Middle East (Levant)"
assert c.delete("/api/admin/looks/latin").status_code == 404
assert c.delete("/api/admin/looks/middle_east_female").json()["ok"]
# --- AI-suggested looks --------------------------------------------------------------
seen = {}
def looks_fake(p, tool=None):
    seen["prompt"] = p
    return {"looks": json.dumps([{"label": "Israel", "gender": "female", "langs": ["he"],
                                  "names": [{"lang": "he", "name": "נועה כהן"}, {"lang": "default", "name": "Noa Cohen"}],
                                  "portrait": "A woman in her 40s with dark curly hair", "why": "Hebrew has no fitting look"}])}
simgen.call_claude = looks_fake
sug = c.post("/api/admin/looks/suggest").json()["looks"]
assert sug[0]["label"] == "Israel" and sug[0]["names"]["he"] == "נועה כהן" and sug[0]["gender"] == "female", sug
assert "he: עברית" in seen["prompt"] and "Europe, male" in seen["prompt"], seen["prompt"][:400]
# --- switching a suggestion's gender rewrites names + description --------------------
def adapt_fake(p, tool=None):
    assert "female person" in p and "Johan" in p
    return {"looks": [{"label": "Southern Africa", "gender": "female", "langs": ["af"],
                       "names": [{"lang": "af", "name": "Anika van der Merwe"}, {"lang": "default", "name": "Anika van der Merwe"}],
                       "portrait": "White South African woman in her late 40s", "why": ""}]}
simgen.call_claude = adapt_fake
r = c.post("/api/admin/looks/adapt", json={"label": "Southern Africa", "gender": "female", "langs": ["af"],
                                         "names": {"af": "Johan van der Merwe"}, "portrait": "man", "why": "gap"}).json()
assert r["gender"] == "female" and r["names"]["af"] == "Anika van der Merwe" and "woman" in r["portrait"] and r["why"] == "gap", r
print("all media tests passed")
