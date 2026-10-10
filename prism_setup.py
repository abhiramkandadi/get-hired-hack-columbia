"""One-time PRISM project setup for CHRP Mesh Messenger. Safe to run twice.

  python prism_setup.py --check     show what exists, change nothing
  python prism_setup.py             create what is missing
  python prism_setup.py --remove    delete what this script made

It makes three things in the PRISM project that the .env keys point at:
  1. An evaluator and an evaluation task named "Caption Score Rubric".
  2. The Knowledge Base document kb/meshtastic-presets.md.
And it checks that the Prompt Injection guardrail rule is on. It never edits a guardrail.
"""
import os
import sys

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))
HOST = os.environ["PRISMTRACE_HOST"].rstrip("/")
PID = os.environ["PRISMTRACE_PROJECT_ID"]
H = {"X-PRISMtrace-Key": os.environ["PRISMTRACE_API_KEY"]}

NAME = "Caption Score Rubric"
KB_FILE = os.path.join(HERE, "kb", "meshtastic-presets.md")

TEMPLATE = (
    "You grade the confidence score that an AI gave to an image caption. You cannot see the image. "
    "You grade only whether the score is well formed and supported by its own reason.\n\n"
    "Caption and request:\n{input}\n\nScore output:\n{output}\n\n"
    "Apply the rubric. Label the result pass if the rubric is met. Otherwise label it fail."
)
RUBRIC = (
    "Score from 0 to 100. Start at 100 and subtract points. "
    "Subtract 40 if the output is not a JSON object with a numeric score between 0 and 1 and a reason string. "
    "Subtract 30 if the reason is empty, vague, or longer than two sentences. "
    "Subtract 30 if the score does not match the reason: a score of 0.8 or more needs a reason that says the claims "
    "are visible, and a score of 0.4 or less needs a reason that names a mismatch. "
    "Subtract 20 if the reason names details that are not in the caption. "
    "A score of 70 or more passes."
)


def call(method, path, **kw):
    r = requests.request(method, f"{HOST}{path}", headers=H, timeout=60, **kw)
    r.raise_for_status()
    return r.json()


def find(path, key, name):
    return next((x for x in call("GET", f"{path}?project_id={PID}")[key] if x["name"] == name), None)


def check():
    ev, task = find("/api/evaluators", "evaluators", NAME), find("/api/eval-tasks", "tasks", NAME)
    docs = call("GET", f"/api/knowledge-base/documents?project_id={PID}")["documents"]
    kb = next((d for d in docs if d.get("name") == os.path.basename(KB_FILE)), None)
    groups = call("GET", f"/api/guardrails/groups?project_id={PID}")["groups"]
    inj = next((r for g in groups if g["enabled"] for r in g["rules"] if r["rule_type"] == "prompt_injection" and r["enabled"]), None)
    print(f"evaluator '{NAME}':       {'present' if ev else 'MISSING'}")
    print(f"evaluation task '{NAME}': {'present' if task else 'MISSING'}")
    print(f"knowledge base document:  {'present' if kb else 'MISSING'} ({os.path.basename(KB_FILE)})")
    print(f"prompt injection guardrail: {inj['action'] + ' / ' + inj['severity'] if inj else 'OFF OR MISSING. Turn it on in the PRISM dashboard under Guardrails.'}")
    return ev, task, kb


def apply():
    ev, task, kb = check()
    if not ev:
        ev = call("POST", "/api/evaluators", json={"project_id": PID, "name": NAME, "type": "llm_judge",
                  "description": "Checks that the caption confidence score is well formed and matches its reason.",
                  "template_prompt": TEMPLATE, "rails": ["fail", "pass"]})
        print("created evaluator")
    call("PATCH", f"/api/evaluators/{ev['id']}", json={"project_id": PID, "scoring_rubric": RUBRIC, "pass_threshold": 70,
         "judge_temperature": 0.0})
    if not task:
        call("POST", "/api/eval-tasks", json={"project_id": PID, "name": NAME, "evaluator_id": ev["id"]})
        print("created evaluation task")
    if not kb:
        with open(KB_FILE, "rb") as f:
            r = requests.post(f"{HOST}/api/knowledge-base/documents", headers=H, timeout=60, data={"project_id": PID},
                              files={"file": (os.path.basename(KB_FILE), f.read(), "text/markdown")})
        r.raise_for_status()
        print("uploaded knowledge base document:", r.json())
    print("\nafter:")
    check()


def remove():
    ev, task, kb = check()
    if task:
        call("DELETE", f"/api/eval-tasks/{task['id']}?project_id={PID}")
    if ev:
        call("DELETE", f"/api/evaluators/{ev['id']}?project_id={PID}")
    if kb:
        call("DELETE", f"/api/knowledge-base/documents/{kb['id']}?project_id={PID}")
    print("removed")


if __name__ == "__main__":
    {"--check": check, "--remove": remove}.get(sys.argv[1] if len(sys.argv) > 1 else "", apply)()
