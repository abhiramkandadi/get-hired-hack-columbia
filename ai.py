"""Nicrron AI calls (caption, caption check) and PRISM traces."""
import base64
import json
import logging
import os
import re
import threading
import time

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
log = logging.getLogger("ai")

MODEL = "openai/gpt-4o-mini"
_client = None
_trace_threads = []


def client():
    global _client
    if _client is None:
        _client = OpenAI(base_url="https://api.nicrron.ai/v1", api_key=os.environ["NICRRON_API_KEY"])
    return _client


def _image_part(image_bytes):
    b64 = base64.b64encode(image_bytes).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def _for_trace(messages):
    """PRISM wants content as a plain string. Text parts are joined. An image becomes a short note."""
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            content = "\n".join(
                f"[image, {len(p['image_url']['url'])} chars of base64]" if p["type"] == "image_url" else p["text"]
                for p in content
            )
        out.append({"role": m["role"], "content": content})
    return out


def _post_trace(trace):
    try:
        host = os.environ.get("PRISMTRACE_HOST", "https://prism.blockconvey.com").rstrip("/")
        r = requests.post(f"{host}/api/traces", json=trace, timeout=15,
                          headers={"X-PRISMtrace-Key": os.environ["PRISMTRACE_API_KEY"]})
        if r.status_code >= 300:
            log.warning("PRISM trace %s failed: %s %s", trace["agent_id"], r.status_code, r.text[:200])
        else:
            log.info("PRISM trace %s sent: %s", trace["agent_id"], r.status_code)
    except Exception as e:
        log.warning("PRISM trace %s failed: %s", trace.get("agent_id"), e)


def send_trace(agent, session_id, messages, output, latency_ms):
    """Send a PRISM trace from a background thread. Never raises."""
    try:
        trace = {
            "project_id": os.environ["PRISMTRACE_PROJECT_ID"],
            "model": MODEL,
            "input_messages": _for_trace(messages),
            "output_message": str(output),  # always a plain string
            "latency_ms": latency_ms,
            "agent_id": agent,
            "agent_name": agent,
            "session_id": session_id,
        }
        t = threading.Thread(target=_post_trace, args=(trace,), daemon=True)
        t.start()
        _trace_threads.append(t)
    except Exception as e:
        log.warning("PRISM trace %s not sent: %s", agent, e)


def flush_traces(timeout=20):
    """Wait for pending traces. Call this before the program exits."""
    for t in _trace_threads:
        t.join(timeout)
    _trace_threads.clear()


def _chat(agent, session_id, messages, max_tokens):
    start = time.time()
    resp = client().chat.completions.create(model=MODEL, messages=messages, max_tokens=max_tokens)
    latency_ms = int((time.time() - start) * 1000)
    text = (resp.choices[0].message.content or "").strip()
    send_trace(agent, session_id, messages, text, latency_ms)
    return text


def caption(image_bytes, session_id):
    """One short factual sentence, under 90 characters."""
    messages = [{"role": "user", "content": [
        {"type": "text", "text": (
            "Write one short factual sentence about this photo, under 90 characters. "
            "Say only what you can see. No adjectives that are not visible facts. "
            "Reply with the sentence only.")},
        _image_part(image_bytes),
    ]}]
    text = _chat("caption-agent", session_id, messages, max_tokens=60).strip('"')
    if len(text) > 90:
        cut = text[:90].rsplit(" ", 1)[0].rstrip(",;:")
        log.warning("Caption was %d characters. Cut to %d at a word boundary.", len(text), len(cut))
        text = cut
    return text


def check_caption(image_bytes, caption_text, session_id):
    """Returns (score 0 to 1, one sentence of reasoning)."""
    messages = [{"role": "user", "content": [
        {"type": "text", "text": (
            f'Caption: "{caption_text}"\n'
            "Grade how well this caption matches the photo. "
            "1 means every claim is visible in the photo. 0 means it does not match. "
            'Reply with JSON only: {"score": <number 0 to 1>, "reason": "<one sentence>"}')},
        _image_part(image_bytes),
    ]}]
    text = _chat("caption-checker", session_id, messages, max_tokens=120)
    found = re.search(r"\{.*\}", text, re.S)
    if not found:
        raise ValueError(f"Caption check did not return JSON: {text!r}")
    data = json.loads(found.group(0))
    return max(0.0, min(1.0, float(data["score"]))), str(data["reason"])
