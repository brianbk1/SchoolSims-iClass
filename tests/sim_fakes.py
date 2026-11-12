"""Fake model replies for testing the sim builder without API calls."""
import json
import re


def fake_sim(prompt: str, tool=None) -> dict:
    """Builds a sim matching the requested structure lines in the prompt."""
    decs = []
    for m in re.finditer(r"Decision (\d+): exactly (\d+) options and exactly (\d+) track\(s\), each track exactly (\d+) slide",
                         prompt):
        num, k, t, s = map(int, m.groups())
        decs.append({
            "short_title": f"Step {num}",
            "prompt": f"[[FEATURED]] is waiting. What do you do at step {num}?",
            "options": [{"label": f"Option {o + 1} at step {num}", "track": (o % t) + 1,
                         "pros": ["Fast"], "cons": ["Risky"],
                         "impacts": [{"key": "Trust", "direction": "up", "body": "Builds trust"},
                                     {"key": "policy_risk", "direction": "down", "body": "Some risk"}]}
                        for o in range(k)],
            "tracks": [{"slides": [{"kind": ["outcome", "character", "scene"][i % 3], "title": f"T{tr} slide {i}",
                                    "body": f"[[FEATURED]] reacts ({num}.{tr}.{i}).",
                                    "image_prompt": "A quiet school office", "image_alt": "An empty school office"}
                                   for i in range(s)]} for tr in range(t)],
        })
    if tool and tool.get("name") == "submit_extension" or "NEW decision point" in prompt:
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
        "decisions": decs,
        "summary": {"title": "Reflect", "body": "Debrief.", "question": "What mattered most?", "takeaways": ["A", "B"]},
        "review_notes": ["Check district policy wording."],
    }


def fake_translate(prompt: str) -> dict:
    rows = json.loads(prompt.split("Fields to write (JSON):\n", 1)[1])
    return {"strings": {r["path"]: {"text": "ES " + r["english"], "back_translation": r["english"]} for r in rows},
            "review_notes": ["Check vos/tú."]}


