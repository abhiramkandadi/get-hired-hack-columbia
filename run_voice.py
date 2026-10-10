"""Step 4 demo: send a voice note from A to E. Transcript first, then Codec2 (or Opus) audio."""
import hashlib
import logging
import os
import sys

import ai
import media
from mesh import Mesh
from transfer import TransferStats, send_message


def main(path, seed=42, verbose=False):
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    raw = open(path, "rb").read()
    base_id = int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") & ~0xF
    transcript_id = base_id

    text = ai.transcribe(raw, f"{transcript_id:08x}", os.path.basename(path))
    audio, codec = media.encode_voice(path)
    media.print_voice_table(path, audio, codec)
    print(f'Transcript (AI made this): "{text}"\n')

    mesh = Mesh(seed=seed, preset="Medium Fast", loss=0.20)
    stats = TransferStats()
    log = (lambda m: print(f"[{mesh.clock.now:8.3f}s] {m}")) if verbose else (lambda m: None)
    print(f"Seed {seed}. Loss 20% per link. Route A -> E. Running at {mesh.clock.speed:g}x real time.")

    air = {}
    t = {}
    t0 = mesh.clock.now
    r1 = send_message(mesh, "A", "E", text.encode("utf-8"), stats, log, msg_id=transcript_id)
    air["transcript"], t["transcript"] = mesh.stats.bytes_on_air, mesh.clock.now - t0
    print(f"Transcript at E after {t['transcript']:.1f} simulated seconds: {r1.status}. "
          f"Text matches: {r1.body.decode('utf-8') == text}")
    if not r1.delivered:
        return
    open("received_transcript.txt", "w", encoding="utf-8").write(r1.body.decode("utf-8"))

    t1 = mesh.clock.now
    r2 = send_message(mesh, "A", "E", audio, stats, log, msg_id=base_id + 1)
    air["audio"] = mesh.stats.bytes_on_air - air["transcript"]
    t["audio"] = mesh.clock.now - t1
    print(f"Audio at E after {mesh.clock.now - t0:.1f} simulated seconds in total "
          f"({t['audio']:.1f} for the audio): {r2.status}. Bytes match: {r2.body == audio}")
    if not r2.delivered:
        return

    fname = "received_voice" + media.voice_extension(codec)
    open(fname, "wb").write(r2.body)
    wav = media.decode_voice(r2.body)
    open("received_voice_playable.wav", "wb").write(wav)
    secs = media.audio_seconds("received_voice_playable.wav")
    print(f"Saved {fname} and received_voice_playable.wav. It decodes to {secs:.1f} s of audio "
          f"(original {media.audio_seconds(path):.1f} s).")

    print()
    print(f"Original audio:   {len(raw)} bytes")
    print(f"Encoded audio:    {len(audio)} bytes ({codec})")
    print(f"Transcript:       {len(text.encode('utf-8'))} bytes")
    print(f"Bytes on air:     transcript {air['transcript']}, audio {air['audio']}, total {mesh.stats.bytes_on_air}")
    print(f"Resends {stats.resends}, polls asked again {stats.reasks}. "
          f"Simulated seconds: transcript {t['transcript']:.1f}, audio {t['audio']:.1f}, total {mesh.clock.now:.1f}")
    ai.flush_traces()


if __name__ == "__main__":
    main(sys.argv[1], verbose="-v" in sys.argv)
