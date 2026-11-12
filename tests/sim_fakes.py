"""Fake model replies for testing the sim builder without API calls."""
import json
import re


def fake_sim(prompt: str, tool=None) -> dict:
    """Outline call: decisions matching the requested structure. Tracks call: slides."""
    name = (tool or {}).get("name", "")
    if name == "submit_tracks":
        m = re.search(r"Write exactly (\d+) track\(s\), each with exactly (\d+) slide", prompt)
        t, s = map(int, m.groups())
        num = int(re.search(r"decision point (\d+):", prompt).group(1))
        return {"tracks": [{"video": {"script": f"I hear you. That changes things ({num}.{tr}). Let's see.", "quote": "I hear you.",
                                      "watch_for": "Their tone."},
                            "slides": [{"kind": ["outcome", "character", "scene"][i % 3], "title": f"T{tr} slide {i}",
                                        "body": f"[[FEATURED]] reacts ({num}.{tr}.{i}).",
                                        "image_prompt": "A quiet school office", "image_alt": "An empty school office"}
                                       for i in range(s)]} for tr in range(t)]}
    decs = []
    for m in re.finditer(r"Decision (\d+): exactly (\d+) options and exactly (\d+) track\(s\)", prompt):
        num, k, t = map(int, m.groups())
        decs.append({
            "short_title": f"Step {num}",
            "prompt": f"[[FEATURED]] is waiting. What do you do at step {num}?",
            "options": [{"label": f"Option {o + 1} at step {num}", "track": (o % t) + 1,
                         "pros": ["Fast"], "cons": ["Risky"],
                         "impacts": [{"key": "Trust", "direction": "up", "body": "Builds trust"},
                                     {"key": "policy_risk", "direction": "down", "body": "Some risk"}]}
                        for o in range(k)],
            "track_summaries": [f"What happens on track {tr + 1}" for tr in range(t)],
        })
    if name == "submit_extension":
        return {"decisions": decs, "summary": {"title": "Wrap up", "body": "All of it.", "question": "Now what?",
                                               "takeaways": ["One", "Two"]}}
    return {
        "title": "Parent Concern: Feeling Singled Out",
        "context_summary": "A principal responds to a parent whose child felt singled out in class.",
        "impact_dimensions": [{"key": "trust", "label": "Trust"}, {"key": "policy_risk", "label": "Policy risk"},
                              {"key": "student_wellbeing", "label": "Student wellbeing"}],
        "intro": {"title": "A difficult voicemail", "body": "You are the principal.", "learning_objective": "Balance it.",
                  "image_prompt": "A principal's office in the morning", "image_alt": "A principal's office"},
        "featured_slide": {"title": "Meet the parent", "body": "[[FEATURED]] calls, upset.",
                           "character_title": "Parent of a 7th grader", "point1": "Child upset", "point2": "Second complaint"},
        "cast_note": "[[FEATURED]] is the parent.",
        "intro_video": {"script": "Thank you for seeing me. My child came home in tears.", "quote": "My child came home in tears.",
                        "watch_for": "How upset they are."},
        "decisions": decs,
        "summary": {"title": "Reflect", "body": "Debrief.", "question": "What mattered most?", "takeaways": ["A", "B"]},
        "review_notes": ["Check district policy wording."],
    }


def fake_translate(prompt: str) -> dict:
    rows = json.loads(prompt.split("Fields to write (JSON):\n", 1)[1])
    return {"strings": {r["path"]: {"text": "ES " + r["english"], "back_translation": r["english"]} for r in rows},
            "review_notes": ["Check vos/tú."]}


