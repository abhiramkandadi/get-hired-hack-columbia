"""Nicrron AI calls (caption, caption check) and PRISM traces."""
import base64
import hashlib
import io
import json
import logging
import os
import re
import threading
import time

import requests
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

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


def _post_trace(trace, handle=None):
    try:
        host = os.environ.get("PRISMTRACE_HOST", "https://prism.blockconvey.com").rstrip("/")
        r = requests.post(f"{host}/api/traces", json=trace, timeout=15,
                          headers={"X-PRISMtrace-Key": os.environ["PRISMTRACE_API_KEY"]})
        if r.status_code >= 300:
            log.warning("PRISM trace %s failed: %s %s", trace["agent_id"], r.status_code, r.text[:200])
        else:
            log.info("PRISM trace %s sent: %s", trace["agent_id"], r.status_code)
            if handle is not None:
                handle["row"] = r.json()   # PRISM stores the trace and answers with its row, including its id
    except Exception as e:
        log.warning("PRISM trace %s failed: %s", trace.get("agent_id"), e)


def send_trace(agent, session_id, messages, output, latency_ms, model=MODEL, handle=None):
    """Send a PRISM trace from a background thread. Never raises.
    If handle is a dict, it gets "thread" (the sender) and later "row" (what PRISM stored)."""
    try:
        trace = {
            "project_id": os.environ["PRISMTRACE_PROJECT_ID"],
            "model": model,
            "input_messages": _for_trace(messages),
            "output_message": str(output),  # always a plain string
            "latency_ms": latency_ms,
            "agent_id": agent,
            "agent_name": agent,
            "session_id": session_id,
        }
        t = threading.Thread(target=_post_trace, args=(trace, handle), daemon=True)
        t.start()
        if handle is not None:
            handle["thread"] = t
        _trace_threads.append(t)
    except Exception as e:
        log.warning("PRISM trace %s not sent: %s", agent, e)


def flush_traces(timeout=20):
    """Wait for pending traces. Call this before the program exits."""
    for t in _trace_threads:
        t.join(timeout)
    _trace_threads.clear()


def _chat(agent, session_id, messages, max_tokens, handle=None):
    start = time.time()
    resp = client().chat.completions.create(model=MODEL, messages=messages, max_tokens=max_tokens)
    latency_ms = int((time.time() - start) * 1000)
    text = (resp.choices[0].message.content or "").strip()
    send_trace(agent, session_id, messages, text, latency_ms, handle=handle)
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


def check_caption(image_bytes, caption_text, session_id, handle=None):
    """Returns (score 0 to 1, one sentence of reasoning)."""
    messages = [{"role": "user", "content": [
        {"type": "text", "text": (
            f'Caption: "{caption_text}"\n'
            "Grade how well this caption matches the photo. "
            "1 means every claim is visible in the photo. 0 means it does not match. "
            'Reply with JSON only: {"score": <number 0 to 1>, "reason": "<one sentence>"}')},
        _image_part(image_bytes),
    ]}]
    text = _chat("caption-checker", session_id, messages, max_tokens=120, handle=handle)
    found = re.search(r"\{.*\}", text, re.S)
    if not found:
        raise ValueError(f"Caption check did not return JSON: {text!r}")
    data = json.loads(found.group(0))
    return max(0.0, min(1.0, float(data["score"]))), str(data["reason"])


IMAGE_MODEL = "google/nano-banana"
IMAGE_SIZE = 512
BASE_URL = "https://api.nicrron.ai/v1"
DESCRIBE_PROMPT = (
    "Describe this image for an image generator, in under 400 characters. Cover the subject, "
    "layout, colours, lighting and composition. Say only what is visible. "
    "Reply with the description only.")


def plain_upscale(received_webp):
    """No AI. Smooth resize to 512x512. Returns PNG bytes."""
    img = Image.open(io.BytesIO(received_webp)).convert("RGB")
    out = io.BytesIO()
    img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.LANCZOS).save(out, "PNG")
    return out.getvalue()


def _describe(received_webp, session_id):
    """Step 1: gpt-4o-mini sees only the received bytes and writes a description."""
    b64 = base64.b64encode(received_webp).decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": DESCRIBE_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/webp;base64,{b64}"}},
    ]}]
    text = _chat("describe-agent", session_id, messages, max_tokens=200).strip('"')
    if not text:
        raise ValueError("empty description")
    return text[:400]


def _generate(description, poll_every=2.0, give_up_after=120.0):
    """Step 2: nano-banana makes an image from the text. Async job: submit, poll, download."""
    headers = {"Authorization": f"Bearer {os.environ['NICRRON_API_KEY']}"}
    r = requests.post(f"{BASE_URL}/images/generations", headers=headers, timeout=30,
                      json={"model": IMAGE_MODEL, "prompt": description})
    r.raise_for_status()
    job_id = r.json()["id"]
    deadline = time.time() + give_up_after
    while time.time() < deadline:
        time.sleep(poll_every)
        s = requests.get(f"{BASE_URL}/images/generations/{job_id}", headers=headers, timeout=30)
        s.raise_for_status()
        status = s.json().get("status")
        if status == "succeeded":
            c = requests.get(f"{BASE_URL}/images/generations/{job_id}/content", headers=headers, timeout=60)
            c.raise_for_status()
            img = Image.open(io.BytesIO(c.content)).convert("RGB")
            out = io.BytesIO()
            img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.LANCZOS).save(out, "PNG")
            return out.getvalue()
        if status in ("failed", "expired"):
            raise RuntimeError(f"image job {status}: {s.json().get('error')}")
    raise TimeoutError(f"image job {job_id} not done after {give_up_after:.0f} s")


def _generate_retry(description):
    """Nicrron sometimes answers 410 Gone for a finished job. Resubmit the job once, then give up."""
    try:
        return _generate(description)
    except requests.HTTPError as e:
        if e.response is None or e.response.status_code != 410:
            raise
        log.warning("Nicrron answered 410 Gone. Resubmitting the image job once.")
        return _generate(description)


def reconstruct(received_webp_bytes, session_id, arrived_bytes, arrived_sha256=None):
    """Rebuild a 512x512 picture at the receiver from only the bytes that arrived.
    Step 1 describes the received image. Step 2 makes a new image from the description.
    The result is a new AI image. It is not the real photo.
    Returns (png_bytes, description, source). source is "AI" or "no AI, plain upscale".
    Never raises. This function never sees the original photo."""
    # Guard: the input cannot be bigger than, or different from, what came over the mesh.
    assert len(received_webp_bytes) <= arrived_bytes, \
        f"reconstruct got {len(received_webp_bytes)} bytes but only {arrived_bytes} arrived"
    if arrived_sha256 is not None:
        assert hashlib.sha256(received_webp_bytes).hexdigest() == arrived_sha256, \
            "reconstruct input is not the layer that arrived"
    description = ""
    try:
        description = _describe(received_webp_bytes, session_id)
        start = time.time()
        try:
            png = _generate_retry(description)
        except Exception:
            send_trace("reconstruct-agent", session_id, [{"role": "user", "content": description}],
                       "failed, fell back to plain upscale", int((time.time() - start) * 1000), IMAGE_MODEL)
            raise
        w, h = Image.open(io.BytesIO(png)).size
        send_trace("reconstruct-agent", session_id, [{"role": "user", "content": description}],
                   f"returned {w}x{h} image, {len(png)} bytes", int((time.time() - start) * 1000), IMAGE_MODEL)
        return png, description, "AI"
    except Exception as e:
        log.warning("Reconstruct failed (%s). Using plain upscale.", e)
        try:
            return plain_upscale(received_webp_bytes), description, "no AI, plain upscale"
        except Exception as e2:
            log.warning("Plain upscale failed too: %s", e2)
            return b"", description, "failed"


WHISPER_MODEL = "openai/whisper-1"


def transcribe(audio_bytes, session_id, filename="voice.m4a"):
    """Speech to text with whisper-1 through the Nicrron gateway. Returns the text."""
    start = time.time()
    resp = client().audio.transcriptions.create(model=WHISPER_MODEL, file=(filename, audio_bytes))
    text = (resp.text or "").strip()
    send_trace("transcript-agent", session_id,
               [{"role": "user", "content": f"[audio file {filename}, {len(audio_bytes)} bytes]"}],
               text, int((time.time() - start) * 1000), WHISPER_MODEL)
    return text
