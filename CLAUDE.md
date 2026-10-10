# CHRP Mesh Messenger: project rules for Claude Code

Style note: the simple-english rules apply to README and Devpost text only. They do not apply to this file or to code.

## What we build
A messaging app that sends text, a voice note and a photo across a simulated LoRa mesh of 5 phones (A, B, C, D, E). Everything runs on one computer. No real radios.

Hackathon: Get Hired Hack @ Columbia Startup Lab, CHRP track. Theme: AI You Can Trust.
Hard deadline: Devpost submission at 4:30 PM ET, Oct 10 2026. Feature freeze at 3:45 PM.

## Judges score
- Media through per byte sent.
- Recovery from lost packets (20% loss, 3+ hops).
- Honest delivery status. Never say Delivered unless the final hash check passed.
- General: technical execution, trust and explainability, design, uniqueness.

## Fixed numbers
- Max LoRa packet: 230 bytes total, header included. Keep our header at 12 bytes or less.
- Data rates (Meshtastic presets): Short Turbo 21.88 kbps, Short Fast 10.94, Medium Fast 3.52, Long Fast 1.07, Long Moderate 0.34. Default for the demo: Medium Fast.
- Airtime per packet: use the Semtech LoRa time-on-air formula with the preset's SF, bandwidth and coding rate.
- Loss per link: grows with distance between phones. Links past max range do not exist.
- Use a fixed random seed (default 42) so every run is the same.
- Simulation runs faster than real time. The UI must always show the speed factor, for example "Running at 10x real time".

## Design rules
- Store and forward: each hop gets the full set of pieces, checks it, then sends it on. Each hop handles its own loss and resends.
- Pieces: numbered, each with a CRC. A bad CRC means the piece is lost. Duplicates are ignored. Receiver sends a bitmap of missing pieces. Sender resends only those. The whole file has a hash, checked at the end.
- ACKs can get lost. The sender times out and asks again. The receiver ignores extra copies.
- If a phone dies, the last phone that holds the full file finds a new route and continues.
- Status states: Queued, Sending, At Phone X (holds all pieces), Receiving n of m, Checking, Delivered, Stuck (with the reason). Time left is a range: best case to likely case at the current loss.
- Photo is sent in four layers. All image layers are real pixels from the photo. Each layer travels over the mesh as packets.
  1. Caption: 1 packet, AI text.
  2. WebP at 32x32, about 400 bytes.
  3. WebP at 64x64, about 1200 bytes.
  4. WebP at 128x128. If time allows, send it.
  The receiver always shows the best layer it has and says which layer it is.
- AI enhancement: the receiver can upscale whatever layer it received, using only the bytes it received.
  - Never send the original photo to a model to make the enhanced version.
  - The enhanced image is created at the destination. It never travels over the mesh.
  - The UI shows received and enhanced side by side, with a toggle.
  - Label for the received image: "Received: N bytes, real pixels".
  - Label for the enhanced image: "AI enhanced from those N bytes. Detail is guessed, not transmitted."
- Transport seam: every packet leaves a phone through one method, link.send(packet_bytes).
  - The simulator implements it with airtime delay and a loss roll.
  - A real LoRa radio will implement the same method over a serial port.
  - Keep this seam in its own small class, with nothing else in it. We point at it to show the protocol is radio-ready.
- Voice: transcript first (1 to 2 packets), then Codec2 audio. If Codec2 cannot be installed, use Opus at 6 kbps and say so in the UI.
- Any AI-made content (caption, transcript, AI-enhanced image) has a clear "AI made this" label. An AI-enhanced image is never shown as the real photo.
- Stats panel: original size, compressed size, total bytes on air (including resends and ACKs), number of resends.

## Sponsors (both are required by the rules)
NicrronNinoo: all AI calls go through the Nicrron gateway. OpenAI-compatible.
- base_url = https://api.nicrron.ai/v1, key in env var NICRRON_API_KEY.
- Photo caption: model "openai/gpt-4o-mini" with the image.
- Caption check: a second call that grades whether the caption matches the image. Score 0 to 1, shown in the UI.
- Voice transcript: model "openai/whisper-1". Check https://nicrron.ai/docs/audio for the endpoint.
- Optional extra: "google/nano-banana" to enhance the received layer at the destination. Input is only the bytes that arrived (see AI enhancement above).

Block Convey PRISM: every AI call sends a trace.
- POST https://prism.blockconvey.com/api/traces with header X-PRISMtrace-Key (not Bearer).
- Required fields: project_id, model, input_messages, output_message, latency_ms.
- In ai.py, output_message is always a plain string.
- Always send agent_id and agent_name (for example "caption-agent", "transcript-agent", "caption-checker") and session_id = the message id.
- Env vars: PRISMTRACE_HOST, PRISMTRACE_PROJECT_ID, PRISMTRACE_API_KEY. Python SDK: pip install "prismtrace-sdk>=0.5.1", import prismtrace.
- Send traces from a background thread so they never slow the app.
- A failed trace must never crash the app. Log it and continue.

## Code layout
- mesh.py: phones, links, loss, airtime, sim clock, routing. Also holds the small link class with send(packet_bytes).
- transfer.py: pieces, CRC, missing-piece bitmap, resends, final hash. Written from scratch today.
- media.py: photo layers, voice encode, size numbers.
- ai.py: Nicrron calls and PRISM traces.
- server.py: FastAPI plus one WebSocket that streams events to the page.
- web/index.html: one page. Phone-style chat on the left, map on the right, controls on top.

## Working rules
- Small steps. After each step, run it and show the output.
- Keys only in .env. Add .env to .gitignore. Make .env.example.
- Keep code simple. No new frameworks unless needed.

## Documentation style
All docs (README, comments in docs, Devpost text) use the simple-english skill from github.com/AminBlg/SimpleEnglish. Short sentences, active voice, no hype words.
