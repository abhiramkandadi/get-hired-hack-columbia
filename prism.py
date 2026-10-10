"""PRISM calls that are not traces: input guardrail, output evaluator, Knowledge Base search.
They use the same key as traces (X-PRISMtrace-Key). Every function returns a small dict and never raises.
Trace sending stays in ai.py. Set up the PRISM side once with prism_setup.py."""
import logging
import os
import re
import threading
import time

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
log = logging.getLogger("prism")

EVAL_TASK = "Caption Score Rubric"   # made by prism_setup.py
DEFAULT_THRESHOLD = 70
_eval_lock = threading.Lock()        # one evaluator run at a time, so each run scores the newest trace


def _call(method, path, timeout=15, **kw):
    host = os.environ.get("PRISMTRACE_HOST", "https://prism.blockconvey.com").rstrip("/")
    r = requests.request(method, f"{host}{path}", headers={"X-PRISMtrace-Key": os.environ["PRISMTRACE_API_KEY"]},
                         timeout=timeout, **kw)
    r.raise_for_status()
    return r.json()


def _pid():
    return os.environ["PRISMTRACE_PROJECT_ID"]


# ---------- 1. Guardrail on the input ----------

def guardrail_check(text, timeout=4):
    """Ask PRISM whether typed text breaks a guardrail rule, before any model can see it.
    status is clean, flagged, blocked or unavailable. The rules live in the PRISM dashboard."""
    try:
        d = _call("POST", "/api/guardrails/test", timeout=timeout, json={"project_id": _pid(), "message": text})
    except Exception as e:
        log.warning("guardrail check failed: %s", e)
        return {"status": "unavailable", "reason": str(e)[:120]}
    flags = d.get("flags") or []
    first = flags[0] if flags else {}
    status = "blocked" if d.get("would_block") else "flagged" if d.get("would_flag") else "clean"
    return {"status": status, "rule": first.get("rule_name"), "rule_type": first.get("rule_type"),
            "severity": first.get("severity"), "matched": first.get("matched_text"),
            "rules": [f.get("rule_name") for f in flags], "latency_ms": d.get("latency_ms")}


# ---------- 2. Evaluator on the scored output ----------

def evaluate_trace(row_id, agent_name, wait=90):
    """Run the "Caption Score Rubric" evaluator on one trace and wait for its verdict.
    row_id is the id PRISM returned when it stored the trace. A run costs PRISM credits."""
    try:
        tasks = _call("GET", f"/api/eval-tasks?project_id={_pid()}")["tasks"]
        task = next((t for t in tasks if t["name"] == EVAL_TASK), None)
        if task is None:
            return {"status": "not_configured"}
        threshold = task.get("pass_threshold")
        if threshold is None:
            evs = _call("GET", f"/api/evaluators?project_id={_pid()}")["evaluators"]
            ev = next((e for e in evs if e["id"] == task["evaluator_id"]), {})
            threshold = ev.get("pass_threshold") or DEFAULT_THRESHOLD
        with _eval_lock:
            _call("POST", f"/api/eval-tasks/{task['id']}/run", timeout=60,
                  params={"project_id": _pid(), "agent_name": agent_name, "since_hours": 1, "limit": 1})
            deadline = time.time() + wait
            while time.time() < deadline:
                time.sleep(2)
                results = _call("GET", "/api/eval-results", params={"project_id": _pid(), "trace_id": row_id})["results"]
                if results:
                    break
            else:
                return {"status": "pending", "evaluator": task["evaluator_name"]}
        r = results[0]
        score = float(r["score"])
        return {"status": "pass" if score >= threshold else "fail", "score": round(score), "threshold": round(threshold),
                "reasoning": r.get("reasoning") or "", "evaluator": r.get("evaluator_name") or task["evaluator_name"]}
    except Exception as e:
        log.warning("evaluator run failed: %s", e)
        return {"status": "unavailable", "reason": str(e)[:120]}


# ---------- 3. A claim grounded in the Knowledge Base ----------

def ground_preset_rate(preset, kbps):
    """Check "<preset> runs at <kbps> kbps" against the PRISM Knowledge Base, not against a model.
    PRISM search is keyword overlap, so we also read the number out of the text we get back."""
    claim = f"{preset} runs at {kbps:g} kbps"
    try:
        d = _call("POST", "/api/knowledge-base/search",
                  json={"query": f"{preset} data rate kbps", "project_id": _pid(), "limit": 3})
    except Exception as e:
        log.warning("knowledge base search failed: %s", e)
        return {"status": "unavailable", "claim": claim, "reason": str(e)[:120]}
    pattern = re.compile(rf"({re.escape(preset)}:[^.]*?data rate ([0-9]+(?:\.[0-9]+)?) kbps)\.")
    for hit in d.get("results", []):
        m = pattern.search(hit.get("content", ""))
        if m:
            found = float(m.group(2))
            return {"status": "grounded" if abs(found - kbps) < 0.005 else "mismatch", "claim": claim,
                    "found": found, "source": hit.get("doc_name"), "snippet": m.group(1) + ".",
                    "method": d.get("method", "keyword_overlap")}
    return {"status": "not_found", "claim": claim}
