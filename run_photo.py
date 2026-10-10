"""Step 2 demo: send a photo as caption + 3 WebP layers from A to E at 20% loss."""
import hashlib
import logging
import os
import sys

import ai
import media
from mesh import Mesh
from transfer import TransferStats, send_message


def rebuild_layer(index, arrived, arrived_sha256, session):
    """Make the upscaled and reconstructed images from the bytes that arrived at E.
    Returns the lines to print."""
    held = bytes(arrived)   # what E holds. The AI calls get this copy and nothing else.
    n = len(held)
    sha = hashlib.sha256(held).hexdigest()
    png, description, source = ai.reconstruct(held, session, arrived_bytes=n, arrived_sha256=arrived_sha256)
    open(f"upscaled_layer_{index}.png", "wb").write(ai.plain_upscale(held))
    open(f"reconstructed_layer_{index}.png", "wb").write(png)
    if source == "AI":
        label = (f"AI reconstructed from a text description of those {n} bytes. "
                 "Not the real photo. Detail is invented, not transmitted.")
    else:
        label = f"Plain upscale of those {n} bytes. No AI, no new detail."
    return (f"    Guard passed: AI input {n} bytes <= {n} bytes that arrived, same sha256 {sha[:12]}, "
            f"original photo not passed.\n"
            f"    Description used ({len(description)} chars): {description or '(none)'}\n"
            f"    Reconstruction source: {source}. Saved received_layer_{index}.webp, "
            f"upscaled_layer_{index}.png, reconstructed_layer_{index}.png\n"
            f'    Label 1: "Received: {n} bytes, real pixels"\n'
            f'    Label 2: "{label}"')


def main(path, seed=42, verbose=False, only=None):
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    original = os.path.getsize(path)
    base_id = int.from_bytes(hashlib.sha256(open(path, "rb").read()).digest()[:4], "big") & ~0xF
    session = f"{base_id:08x}"   # message id of the caption message

    layers = media.make_layers(path)
    text = score = reason = None
    if only is None:
        photo_for_ai = media.for_ai(path)
        text = ai.caption(photo_for_ai, session)
        score, reason = ai.check_caption(photo_for_ai, text, session)
        media.fill_caption(layers, text)
    else:
        layers = [l for l in layers if l.index == only]
    media.print_table(layers, original)
    print()

    mesh = Mesh(seed=seed, preset="Medium Fast", loss=0.20)
    stats = TransferStats()
    quiet = lambda msg: None
    log = (lambda msg: print(f"[{mesh.clock.now:8.3f}s] {msg}")) if verbose else quiet
    print(f"Seed {seed}. Loss 20% per link. Route A -> E. Running at {mesh.clock.speed:g}x real time.")
    print(f"{'Layer':<18} {'At E':>8} {'Bytes on air':>13} {'Sim sec':>8} {'Orig/layer':>11} {'Orig/air':>9}  Status")

    for layer in layers:
        result = send_message(mesh, "A", "E", layer.data, stats, log, msg_id=base_id + layer.index)
        if not result.delivered:
            print(f"Layer {layer.index} {result.status}")
            break
        if layer.index == 0:
            fname = "received_layer_0.txt"
            open(fname, "w", encoding="utf-8").write(result.body.decode("utf-8"))
        else:
            fname = f"received_layer_{layer.index}.webp"
            open(fname, "wb").write(result.body)
        enhanced_note = ""
        if layer.index > 0:
            enhanced_note = rebuild_layer(layer.index, result.body, hashlib.sha256(layer.data).hexdigest(),
                                          f"{base_id + layer.index:08x}")
        air = mesh.stats.bytes_on_air
        print(f"{layer.index} {layer.name:<16} {len(result.body):>8} {air:>13} {mesh.clock.now:>8.1f} "
              f"{original / len(result.body):>10.0f}x {original / air:>8.0f}x  "
              f"{result.status}, saved {fname}, matches sent: {result.body == layer.data}")
        if enhanced_note:
            print(enhanced_note)

    print()
    if text is not None:
        print(f'Caption (AI made this): "{text}" ({len(text)} characters)')
        print(f"Caption check score: {score:.2f}")
        print(f"Reasoning: {reason}")
        print()
    print(f"Totals: original {original} bytes, layers sent {sum(len(l.data) for l in layers)} bytes, "
          f"bytes on air {mesh.stats.bytes_on_air} ({mesh.stats.packets_sent} packets, "
          f"{mesh.stats.packets_lost} lost), resends {stats.resends}, polls asked again {stats.reasks}, "
          f"simulated seconds {mesh.clock.now:.1f}")
    ai.flush_traces()


if __name__ == "__main__":
    only = int(sys.argv[sys.argv.index("--only") + 1]) if "--only" in sys.argv else None
    main(sys.argv[1], verbose="-v" in sys.argv, only=only)
