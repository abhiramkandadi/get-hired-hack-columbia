"""FastAPI server. One WebSocket streams mesh events to the page. Also serves web/index.html.

Run:  python server.py   (then open http://127.0.0.1:8000)
"""
import asyncio
import hashlib
import itertools
import json
import logging
import os
import queue
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

import ai
import media
from mesh import PRESETS, Mesh, airtime, bit_rate
from transfer import HEADER, TransferStats, make_pieces, send_message

log = logging.getLogger("server")
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
SEED = 42
PRESET = "Medium Fast"
DEFAULT_SPEED = 10.0
# Two middle phones (B, C) give the mesh a second route. D is the only way to E.
POSITIONS = {"A": (100, 300), "B": (260, 180), "C": (260, 420), "D": (420, 300), "E": (620, 300)}
SRC = "A"


class Hub:
    """Holds the mesh and the list of events. Threads call emit(). The event loop sends them."""

    def __init__(self):
        self.loop = None
        self.wake = None
        self.pending = queue.Queue()
        self.history = []            # everything except packets, so a new page can catch up
        self.clients = set()
        self.ids = itertools.count(1)
        self.reset()

    def reset(self):
        self.epoch = getattr(self, "epoch", 0) + 1
        self.mesh = Mesh(seed=SEED, preset=PRESET, loss=0.20, speed=DEFAULT_SPEED, positions=POSITIONS)
        self.mesh.clock.sleep = True
        self.mesh.tap = self.tap
        self.tstats = TransferStats()
        self.offline = False
        self.blobs = {}
        self.history.clear()
        self.original_bytes = 0
        self.sent_bytes = 0

    def emit(self, ev):
        ev["t"] = round(self.mesh.clock.now, 3)
        if ev["type"] != "packet":
            self.history.append(ev)
        self.pending.put(ev)
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.wake.set)

    def tap(self, src, dst, packet_bytes, lost):
        m = self.mesh
        self.emit({"type": "packet", "src": src, "dst": dst, "ptype": packet_bytes[4] if len(packet_bytes) > 4 else 0,
                   "len": len(packet_bytes), "lost": lost,
                   "ms": max(260, int(airtime(len(packet_bytes), m.preset) / m.clock.speed * 1000)),
                   "stats": self.stats()})

    def stats(self):
        s = self.mesh.stats
        return {"sent": s.packets_sent, "lost": s.packets_lost, "air": s.bytes_on_air,
                "resends": self.tstats.resends, "reasks": self.tstats.reasks,
                "original": self.original_bytes, "payload": self.sent_bytes}

    def config(self):
        m = self.mesh
        names = list(m.phones)
        links = []
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pa, pb = m.phones[a], m.phones[b]
                if pa.distance_to(pb) <= pa.range:      # draw the link even when a phone is off
                    links.append({"a": a, "b": b, "loss": round(m.loss_at(pa.distance_to(pb)), 3)})
        return {"type": "config", "phones": {n: {"x": p.x, "y": p.y} for n, p in m.phones.items()},
                "links": links, "dead": sorted(m.dead), "offline": self.offline,
                "speed": m.clock.speed, "loss": m.loss_at_spacing, "preset": m.preset,
                "kbps": round(bit_rate(m.preset) / 1000, 2), "seed": SEED, "src": SRC}

    def blob(self, data, mime):
        key = f"{len(self.blobs) + 1}-{hashlib.sha1(data).hexdigest()[:8]}"
        self.blobs[key] = (data, mime)
        return f"/blob/{key}"


hub = Hub()
jobs = queue.Queue()
app = FastAPI()


# ---------- ETA: best case to likely case, in simulated seconds ----------

def eta_range(mesh, hops_left, pieces_per_hop):
    """hops_left: list of (a, b) still to cross. pieces_per_hop: pieces each of them still needs.
    Best case: no loss. Likely: the current loss on each link."""
    t_data = airtime(230, mesh.preset)
    t_small = airtime(HEADER.size, mesh.preset) + airtime(HEADER.size + 4, mesh.preset)
    best = likely = 0.0
    for (a, b), n in zip(hops_left, pieces_per_hop):
        loss = mesh.loss_at(mesh.phones[a].distance_to(mesh.phones[b]))
        ok = max(0.05, 1 - loss)
        best += n * t_data + t_small
        likely += n * t_data / ok + (t_small + 0.25) / (ok * ok) * 1.5
    return best, likely


class PartTracker:
    """Follows one piece of a message (text, a layer, the audio) and adds time left to each event."""

    def __init__(self, mid, part, later_pieces):
        self.mid, self.part = mid, part
        self.path, self.hop, self.have, self.total = [], 0, 0, 0
        self.later = later_pieces      # pieces in the parts that come after this one

    def __call__(self, kind, **f):
        h = hub
        if h.epoch != self.epoch:
            return
        if kind == "route":
            self.path, self.hop = f["path"], 0
        elif kind == "hop_start":
            self.hop = self.path.index(f["a"]) if f["a"] in self.path else self.hop
            self.have, self.total = 0, f["total"]
        elif kind == "piece":
            self.have, self.total = f["have"], f["total"]
        ev = {"type": kind, "mid": self.mid, "part": self.part, **f}
        if kind in ("route", "hop_start", "piece", "hop_done", "at", "queued") and len(self.path) > 1:
            hops = list(zip(self.path, self.path[1:]))[self.hop:]
            per = [(self.total - self.have) if i == 0 else self.total for i in range(len(hops))]
            if kind in ("hop_done", "at"):
                hops, per = hops[1:], per[1:]
            if self.later:
                per = [p + self.later // max(1, len(self.path) - 1) for p in per]
            if hops:
                best, likely = eta_range(h.mesh, hops, per)
                ev["eta"] = [round(best, 1), round(likely, 1)]
        h.emit(ev)

    epoch = 0


def tracker(mid, part, later_pieces=0):
    t = PartTracker(mid, part, later_pieces)
    t.epoch = hub.epoch
    return t


def note(mid, text, kind="prep"):
    hub.emit({"type": kind, "mid": mid, "text": text})


def deliver(mid, part, data, dst, later_pieces=0):
    """Send one part over the mesh. Returns the Result."""
    h = hub
    h.sent_bytes += len(data)
    t = tracker(mid, part, later_pieces)
    msg_id = (int(mid) << 4 | {"text": 0, "caption": 0, "l1": 1, "l2": 2, "l3": 3, "transcript": 4, "audio": 5}[part]) & 0xFFFFFFFF
    return send_message(h.mesh, SRC, dst, data, h.tstats, lambda m: None, msg_id=msg_id, emit=t, wait_for_route=180)


# ---------- jobs ----------

def job_text(mid, text, dst):
    data = text.encode("utf-8")
    r = deliver(mid, "text", data, dst)
    if r.delivered:
        hub.emit({"type": "text_received", "mid": mid, "text": r.body.decode("utf-8")})
    done(mid, r)


def done(mid, r):
    if not r.delivered:
        hub.emit({"type": "stuck_final", "mid": mid, "reason": r.status})


prep_pool = ThreadPoolExecutor(max_workers=6)      # AI work that must not delay the radio
enhance_pool = ThreadPoolExecutor(max_workers=3)   # slow image jobs at the destination


def make_caption(mid, path):
    """Runs in prep_pool. Returns the caption bytes, or None if the AI call failed."""
    try:
        photo = media.for_ai(path)
        text = ai.caption(photo, f"{mid:08x}")
        score, reason = ai.check_caption(photo, text, f"{mid:08x}")
        hub.emit({"type": "caption_made", "mid": mid, "text": text, "score": score, "reason": reason})
        return text.encode("utf-8")
    except Exception as e:
        log.warning("caption failed: %s", e)
        return None


def make_transcript(mid, raw, name):
    """Runs in prep_pool. Returns the transcript bytes, or None if the AI call failed."""
    try:
        text = ai.transcribe(raw, f"{mid:08x}", name).strip()
        return text.encode("utf-8") if text else None
    except Exception as e:
        log.warning("transcript failed: %s", e)
        return None


def job_photo(mid, path, dst):
    """The radio starts at once with the 32x32 layer. The caption is made on the side
    and goes out at the next layer boundary after it is ready."""
    h = hub
    epoch = h.epoch
    original = os.path.getsize(path)
    layers = media.make_layers(path)[1:]
    cap = None if h.offline else prep_pool.submit(make_caption, mid, path)
    if cap is None:
        note(mid, "Offline: no AI caption.", "notice")
    names = {1: "l1", 2: "l2", 3: "l3"}
    parts = [{"id": "caption", "label": "Caption", "bytes": 0, "pieces": 1}] if cap else []
    parts += [{"id": names[l.index], "label": f"{l.pixels}×{l.pixels}", "bytes": len(l.data),
               "pieces": media.packets_needed(l.data)} for l in layers]
    h.emit({"type": "parts", "mid": mid, "parts": parts})

    def send_caption(block):
        nonlocal cap
        if cap is None or not (block or cap.done()):
            return True
        data, cap = cap.result(), None
        if data is None:
            note(mid, "Caption skipped: the AI could not be reached.", "notice")
            h.emit({"type": "part_skipped", "mid": mid, "part": "caption"})
            return True
        r = deliver(mid, "caption", data, dst)
        if not r.delivered:
            done(mid, r)
            return False
        h.emit({"type": "caption_received", "mid": mid, "text": r.body.decode("utf-8")})
        return True

    for n, layer in enumerate(layers):
        if h.epoch != epoch or not send_caption(False):
            return
        later = sum(media.packets_needed(l.data) for l in layers[n + 1:])
        r = deliver(mid, names[layer.index], layer.data, dst, later)
        if not r.delivered:
            done(mid, r)
            return
        got = bytes(r.body)
        sha = hashlib.sha256(layer.data).hexdigest()
        h.emit({"type": "layer", "mid": mid, "part": names[layer.index], "index": layer.index, "pixels": layer.pixels,
                "bytes": len(got), "original": original,
                "received": h.blob(got, "image/webp"), "upscaled": h.blob(ai.plain_upscale(got), "image/png")})
        enhance_pool.submit(enhance, mid, names[layer.index], layer.index, got, sha, epoch)
    if send_caption(True):
        h.emit({"type": "message_done", "mid": mid})


def enhance(mid, part, index, got, sha, epoch):
    """Runs at the destination, on the bytes that arrived. Never sees the original photo."""
    h = hub
    if h.offline:
        h.emit({"type": "enhanced", "mid": mid, "part": part, "source": "offline", "url": None, "description": ""})
        return
    png, description, source = ai.reconstruct(got, f"{mid:08x}{index}", arrived_bytes=len(got), arrived_sha256=sha)
    if h.epoch != epoch:
        return
    h.emit({"type": "enhanced", "mid": mid, "part": part, "source": source,
            "url": h.blob(png, "image/png") if png and source == "AI" else None, "description": description})


def job_voice(mid, path, dst):
    """The audio goes out at once. The transcript is made on the side. It goes out first
    if it is ready when the audio starts, otherwise right after the audio."""
    h = hub
    with open(path, "rb") as f:
        raw = f.read()
    tr = None if h.offline else prep_pool.submit(make_transcript, mid, raw, os.path.basename(path))
    if tr is None:
        note(mid, "Offline: no AI transcript.", "notice")
    audio, codec = media.encode_voice(path)
    seconds = media.audio_seconds(path)
    parts = [{"id": "transcript", "label": "Transcript", "bytes": 0, "pieces": 1}] if tr else []
    parts.append({"id": "audio", "label": codec, "bytes": len(audio), "pieces": len(make_pieces(audio))})
    h.emit({"type": "parts", "mid": mid, "parts": parts, "codec": codec, "seconds": round(seconds, 1)})

    def send_transcript(block):
        nonlocal tr
        if tr is None or not (block or tr.done()):
            return True
        data, tr = tr.result(), None
        if data is None:
            note(mid, "Transcript skipped: the AI could not be reached.", "notice")
            h.emit({"type": "part_skipped", "mid": mid, "part": "transcript"})
            return True
        r = deliver(mid, "transcript", data, dst)
        if not r.delivered:
            done(mid, r)
            return False
        h.emit({"type": "transcript_received", "mid": mid, "text": r.body.decode("utf-8"), "bytes": len(r.body)})
        return True

    if not send_transcript(False):
        return
    r = deliver(mid, "audio", audio, dst)
    if not r.delivered:
        done(mid, r)
        return
    h.emit({"type": "audio_received", "mid": mid, "url": h.blob(media.decode_voice(r.body), "audio/wav"),
            "codec": codec, "bytes": len(r.body), "seconds": round(seconds, 1)})
    if send_transcript(True):
        h.emit({"type": "message_done", "mid": mid})


def worker():
    while True:
        epoch, kind, mid, arg, dst = jobs.get()
        if epoch != hub.epoch:
            continue
        try:
            if kind == "text":
                job_text(mid, arg, dst)
            elif kind == "photo":
                job_photo(mid, arg, dst)
            else:
                job_voice(mid, arg, dst)
        except Exception as e:
            log.exception("job failed")
            hub.emit({"type": "stuck_final", "mid": mid, "reason": f"Stuck: {e}"})


threading.Thread(target=worker, daemon=True).start()


# ---------- web ----------

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


@app.get("/blob/{key}")
def blob(key: str):
    item = hub.blobs.get(key)
    if item is None:
        return Response(status_code=404)
    return Response(item[0], media_type=item[1], headers={"Cache-Control": "no-store"})


def check_upload(kind, path, data, content_type):
    """Make sure the file opens, and make the preview. Raises if it does not."""
    if kind == "photo":
        return hub.blob(media.for_ai(path, 480), "image/jpeg")
    media.audio_seconds(path)
    return hub.blob(data, content_type or "audio/webm")


@app.post("/api/send")
async def send(kind: str = Form(...), dst: str = Form("E"), text: str = Form(""), file: UploadFile = File(None)):
    if dst not in hub.mesh.phones or dst == SRC:
        return JSONResponse({"error": "Pick a phone other than A."}, status_code=400)
    mid = next(hub.ids)
    arg, original, preview = None, 0, None
    if kind == "text":
        if not text.strip():
            return JSONResponse({"error": "Type a message first."}, status_code=400)
        arg, original = text.strip(), len(text.strip().encode())
    elif kind in ("photo", "voice") and file is not None:
        data = await file.read()
        suffix = os.path.splitext(file.filename or "")[1] or (".jpg" if kind == "photo" else ".webm")
        fd, arg = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        original = len(data)
        try:
            preview = await asyncio.to_thread(check_upload, kind, arg, data, file.content_type)
        except Exception as e:
            os.remove(arg)
            log.warning("upload rejected: %s", e)
            what = "a photo (JPG, PNG or WebP)" if kind == "photo" else "an audio file"
            return JSONResponse({"error": f"The mesh could not read that file as {what}."}, status_code=400)
    else:
        return JSONResponse({"error": "Nothing to send."}, status_code=400)
    hub.original_bytes += original
    hub.emit({"type": "message", "mid": mid, "kind": kind, "dst": dst, "src": SRC, "original": original,
              "text": text.strip() if kind == "text" else "", "preview": preview, "offline": hub.offline})
    jobs.put((hub.epoch, kind, mid, arg, dst))
    return {"mid": mid}


# ---------- websocket ----------

def apply_command(cmd):
    m = hub.mesh
    op = cmd.get("op")
    if op == "loss":
        m.loss_at_spacing = max(0.0, min(0.4, float(cmd["value"])))
    elif op == "speed":
        m.clock.speed = max(1.0, min(200.0, float(cmd["value"])))
    elif op == "toggle_phone" and cmd.get("phone") in m.phones:
        m.dead.symmetric_difference_update({cmd["phone"]})
        hub.emit({"type": "phone", "phone": cmd["phone"], "dead": cmd["phone"] in m.dead})
    elif op == "offline":
        hub.offline = bool(cmd["value"])
    elif op == "reset":
        hub.reset()
        hub.emit({"type": "reset"})
    else:
        return
    hub.emit(hub.config())


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    await sock.send_text(json.dumps(hub.config()))
    for ev in list(hub.history):
        if ev["type"] != "config":
            await sock.send_text(json.dumps(ev))
    hub.clients.add(sock)
    try:
        while True:
            apply_command(json.loads(await sock.receive_text()))
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(sock)


async def pump():
    while True:
        await hub.wake.wait()
        hub.wake.clear()
        batch = []
        while True:
            try:
                batch.append(json.dumps(hub.pending.get_nowait()))
            except queue.Empty:
                break
        for sock in list(hub.clients):
            try:
                for text in batch:
                    await sock.send_text(text)
            except Exception:
                hub.clients.discard(sock)


@app.on_event("startup")
async def startup():
    hub.loop = asyncio.get_running_loop()
    hub.wake = asyncio.Event()
    asyncio.create_task(pump())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), log_level="warning")
