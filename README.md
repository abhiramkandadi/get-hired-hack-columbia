# CHRP Mesh Messenger

This project sends text, a photo, and a voice note across a simulated LoRa mesh of five phones. LoRa is a long range, low power radio. One LoRa packet holds at most 230 bytes. The phones are named A to E. They all run on one computer, and no real radios are involved.

We built it for the CHRP track of the Get Hired Hack at Columbia Startup Lab. The theme is AI You Can Trust. The project asks one question. If the radio loses packets, how little data can we send and still deliver a useful message?

## What it does

You type in a phone-style chat on Phone A. A map shows the five phones and the links between them. Packets move along the links, and lost packets flash red. A mesh is a network where each phone passes messages to its neighbors.

A message crosses the mesh one hop at a time. A hop is one link between two phones. Each phone collects every piece of the message and tests it. A piece is a numbered part of a message. Then the phone sends the pieces to the next phone.

The status line says Delivered only after the SHA-256 hash of the whole message matches at the last phone. A hash is a short fingerprint of data. If the hash does not match, the message does not show as Delivered.

A photo travels in four layers. Layer 0 is a caption of one packet that an AI writes. Layers 1 to 3 are WebP images at 32x32, 64x64, and 128x128 pixels. All three images come from the real photo. The receiver shows the best layer that arrived and says which layer it is.

The receiver can also show an AI reconstruction next to the real pixels. The page labels it "AI made this". It never passes as the real photo.

A voice note travels as a transcript first and as Codec2 audio second. Codec2 is a speech codec for very low bit rates. If Codec2 is not available, the project uses Opus at 6 kbps and says so in the interface.

The top bar sets the packet loss from 0 to 40 percent. It also switches phones off, sets the simulation speed, and has an Offline switch. The page always shows the speed, for example "Running at 10x real time". The Offline switch turns off the AI steps. It does not change the mesh.

## Run it

You need Python 3.10 or newer. You also need ffmpeg on your PATH. Voice notes need ffmpeg. The Codec2 encoder (`libcodec2`) is optional. Without it, voice falls back to Opus.

1. Install the Python packages: `pip install -r requirements.txt`
2. Copy `.env.example` to `.env`.
3. Fill in the four values in `.env`: `NICRRON_API_KEY`, `PRISMTRACE_HOST`, `PRISMTRACE_PROJECT_ID`, and `PRISMTRACE_API_KEY`.
4. Make sure that both keys work: `python check_keys.py`
5. Set up the PRISM project once: `python prism_setup.py`
6. Start the server: `python server.py --demo photo.jpg`
7. Open http://127.0.0.1:8000 in a browser.

Step 5 creates three things in your PRISM project. It creates an evaluator named "Caption Score Rubric" and a task with the same name. It also uploads the Knowledge Base document `kb/meshtastic-presets.md`. Run `python prism_setup.py --check` to see what exists. Run `python prism_setup.py --remove` to delete what the script made. If you skip step 5, the page still works. The PRISM chips then show that the evaluator or the Knowledge Base is not set up.

Each evaluator run costs PRISM credits.

Replace `photo.jpg` with the photo that you will use in the demo. The `--demo` flag turns on demo mode. At startup, demo mode sends that photo once over a private mesh. It takes the bytes that arrive and makes the AI reconstruction from those bytes. It saves the result in `.demo_cache`. When you send the same photo later, the panel appears at once. The panel then says "Cached from an earlier run of the same bytes". The first start takes about one minute. Later starts reuse the cache.

Demo mode also saves the other AI results that the demo needs. These are the caption, its score, the evaluator verdict, the transcript of `voice.m4a`, and the guardrail result for the demo text. The page marks each of them as cached.

## The Run demo button

In demo mode, a Run demo button sits at the top of the page. The button stays grey until the saved results are ready. One click then runs the whole sequence with no more input.

1. The demo sends a text message and waits for Delivered.
2. The demo sends the photo. The caption arrives, then the three layers, then the three panels.
3. The demo sends `voice.m4a`. When the audio is half across, it switches off Phone B. The message finds a new route through Phone C and arrives.

A step indicator shows which of the three stages runs. The run uses seed 42 and the saved results, so it makes no network calls. It takes about 20 seconds at 5x speed. When the run ends, the button turns into Reset. Two runs in a row gave the same numbers: 24,981 bytes on air, 23 resends, and 43 of 214 packets lost.

If you leave out `--demo`, run `python server.py`. Every photo then calls the AI live. Set the `PORT` environment variable to change the port.

You can also run the protocol without the page:

```
python run_text.py
python run_photo.py photo.jpg
python run_voice.py voice.m4a
```

Add `-v` to print every packet.

## Files

Files that you run:

- `server.py`: the FastAPI server. It serves the page, streams mesh events over one WebSocket, and runs the demo mode.
- `run_text.py`, `run_photo.py`, `run_voice.py`: command line demos of one message type each.
- `check_keys.py`: makes sure that the Nicrron and PRISM keys work.
- `prism_setup.py`: creates, shows, or removes the PRISM evaluator, task, and Knowledge Base document.

Files that hold the logic:

- `mesh.py`: phones, links, loss, airtime, the simulation clock, and routing. The `Link` class with its `send` method is the transport seam.
- `transfer.py`: pieces, CRC, the bitmap of missing pieces, resends, and the final hash. It also reroutes around a dead phone.
- `media.py`: photo layers, voice encoding, and the size numbers.
- `ai.py`: the Nicrron calls (caption, caption score, transcript, reconstruction) and the PRISM traces.
- `prism.py`: the PRISM guardrail, evaluator, and Knowledge Base calls.

Other files:

- `web/index.html`: the whole page, in plain HTML, CSS, and JavaScript.
- `kb/meshtastic-presets.md`: the radio presets that the Knowledge Base claim uses.
- `voice.m4a`: a sample voice note.
- `requirements.txt` and `.env.example`: the packages and the key names.
- `CLAUDE.md`: the project rules for the coding assistant.
- `received_*`, `enhanced_*`, `reconstructed_*`, and `upscaled_*`: output that the demo scripts wrote.

## Numbers we measured

We measured these numbers with the code in this repository. Every run uses seed 42, the Medium Fast preset, and a loss of 20 percent on a link 200 units long. Loss grows with the square of the distance, so some links lose more. The route is A to B to D to E, which is three hops. One 230 byte packet takes 0.59 seconds of airtime on Medium Fast.

The test photo is a 4032x3024 JPEG of 2,396,015 bytes. Times are simulated seconds.

| Part | Bytes | Pieces | Bytes on air | Packets lost | Resends | Time |
| --- | --- | --- | --- | --- | --- | --- |
| Caption | 68 | 1 | 703 | 5 of 18 | 2 | 3.5 |
| 32x32 | 400 | 2 | 2,201 | 4 of 22 | 3 | 6.7 |
| 64x64 | 1,206 | 6 | 4,894 | 4 of 34 | 4 | 13.4 |
| 128x128 | 2,974 | 14 | 12,055 | 14 of 72 | 11 | 32.9 |
| Total | 4,648 | 23 | 19,853 | 27 of 146 | 20 | 56.4 |

Bytes on air include resends and the ACKs (the answers that report missing pieces). The whole photo needed 19,853 bytes on air. That is 121 times smaller than the original file. The layers alone are 515 times smaller than the original.

We did not run the original photo over the mesh. We computed the cost from the airtime formula. The original needs 10,941 pieces per hop. That is about 5.4 hours of airtime over three hops with no loss.

The next table sends the same four parts at different loss rates. Every run delivered every part, and every hash matched.

| Loss | Bytes on air | Packets sent | Packets lost | Resends | Polls asked again | Time |
| --- | --- | --- | --- | --- | --- | --- |
| 0% | 15,366 | 93 | 0 | 0 | 0 | 41 |
| 10% | 17,645 | 120 | 12 | 9 | 3 | 49 |
| 20% | 19,853 | 146 | 27 | 20 | 7 | 56 |
| 30% | 23,846 | 189 | 58 | 35 | 23 | 72 |
| 40% | 25,841 | 242 | 93 | 41 | 52 | 88 |

The voice note is 8.3 seconds long. The original `voice.m4a` is 82,426 bytes. Codec2 at 700 bits per second made it 831 bytes in 4 pieces. That is 99 times smaller. The transcript is 48 bytes in 1 piece. Over the mesh at 20 percent loss, both parts needed 4,220 bytes on air. Twelve of 51 packets were lost, and 5 pieces were sent again. The transfer took 14.6 simulated seconds. The bytes on air are 20 times smaller than the original file.

In the browser, the first packet left 128 to 153 milliseconds after the message appeared. The cached AI panel appeared 1 to 4 milliseconds after each layer arrived. One evaluator run returned its verdict about 10 seconds after the caption. That verdict was 85 out of 100.

## Design decisions

Each phone stores the whole message and then forwards it. This is store and forward. A lost packet then costs one resend over one link, not over the whole path. If a phone dies, the last phone that holds the full message finds a new route and continues. We chose this for reliability.

Each piece has a number and a CRC (a short code that detects damaged data). A piece with a bad CRC counts as lost. The receiver ignores duplicate pieces. The sender asks for a bitmap of missing pieces and resends only those. If an ACK is lost, the sender waits, then asks again.

The packet header is 11 bytes. That leaves 219 bytes of payload in a 230 byte packet. The header holds the message id, the type, the piece number, the piece count, and the CRC.

The whole message carries one SHA-256 hash. The app shows Delivered only after that hash matches. We want a status that the user can trust more than a status that looks good.

The photo goes in layers so that something useful arrives early. The 32x32 layer needs 2 pieces. It arrives about 7 simulated seconds after the send starts. If time allows, the better layers follow.

The AI reconstruction runs only at the destination. It uses only the bytes that arrived. If the input is larger than the layer that came over the mesh, a guard in the code stops the call. If the input differs from that layer, the guard also stops the call. The AI image never travels over the mesh. The page shows the real pixels, a plain upscale, and the AI image side by side. Green marks real bytes and violet marks AI output.

Every packet leaves a phone through one method, `link.send(packet_bytes)`. The simulator implements it with an airtime delay and a loss roll. A real radio class can implement the same method over a serial port. We kept the class small so that we can point at it.

Airtime comes from the Semtech LoRa time-on-air formula. It uses the spreading factor, bandwidth, and coding rate of the preset. The default preset is Medium Fast at 3.52 kbps. A fixed seed makes every run the same. The simulation runs faster than real time, and the page always shows the speed.

The AI calls run off the radio path. The first packet of a photo does not wait for the caption. The caption goes out at the next layer boundary after it is ready. A voice note sends its audio first and its transcript after. This changes the "transcript first" order, and we did it to start sending at once.

All AI calls go through the Nicrron gateway. Block Convey PRISM receives a trace of every call. Traces leave from a background thread, and a failed trace never stops the app. PRISM also provides three controls. A guardrail checks typed text for prompt injection before anything else happens. An evaluator grades the caption score against a rubric. A Knowledge Base document backs the data rate that the map header shows.

## Honest limits

- The mesh is simulated. It is not real radios. The loss model is simple and has no interference, no collisions, and no duty cycle limits. The airtime is computed, not measured on hardware.
- The AI reconstruction is not faithful. It builds a new image from a text description of the received bytes. It invents detail. The page labels it as AI made, and the real pixels and the plain upscale are the faithful panels.
- The caption checker shares a model family with the captioner. Both use `openai/gpt-4o-mini`. The score is a sanity check, not an independent verdict. The PRISM evaluator cannot see the image. It judges only whether the score is well formed and matches its own reason.
- Codec2 at 700 bits per second is understandable but rough. In our test it turned "North Gate" into "North Tee".
- Store and forward is slower to the first byte than forwarding pieces as they arrive. A forwarding design can start the next hop before the first hop ends. We chose store and forward for reliability.
- The time left on the status line is a range from a simple estimate. It is not a promise.
- The radio handles one message at a time. A message that is stuck holds up the messages behind it.
- Typed text never reaches a model in this app. The guardrail gates the send path. If PRISM is not reachable, the message still sends, and the page says that it was not tested.
- The PRISM Knowledge Base search matches keywords. It does not use meaning, so the code reads the number out of the returned text.
- The Nicrron image service sometimes answers 410 Gone. The code sends the job again once. After a second failure, the panel says that no AI image was made.
- The code sends PRISM traces with plain HTTP requests. `requirements.txt` lists the PRISM SDK, but the code does not import it.
- The demo cache holds AI images that came from received bytes only. It matches on the SHA-256 hash of those bytes. A different photo goes through the live AI path.
