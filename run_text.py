"""Step 1 demo: send a 500 character text from A to E at 20% loss per link."""
from mesh import Mesh, bit_rate, airtime
from transfer import TransferStats, send_message

TEXT = ("Meshtastic style store and forward test. Each phone collects every piece, "
        "checks it, and only then passes it on. ")
TEXT = (TEXT * 6)[:500]


def main(seed=42):
    mesh = Mesh(seed=seed, preset="Medium Fast", loss=0.20)
    stats = TransferStats()
    log = lambda msg: print(f"[{mesh.clock.now:8.3f}s] {msg}")

    print(f"Preset {mesh.preset}: {bit_rate(mesh.preset) / 1000:.2f} kbps, "
          f"230 byte packet = {airtime(230, mesh.preset):.3f} s on air")
    print(f"Seed {seed}. Loss {mesh.loss_at_spacing:.0%} per link. "
          f"Running at {mesh.clock.speed:g}x real time (time is simulated, no waiting).")
    body = TEXT.encode()
    result = send_message(mesh, "A", "E", body, stats, log)

    print()
    print(f"Final status: {result.status}")
    print(f"Text matches original: {result.body == body}")
    print(f"Original bytes:      {len(body)}")
    print(f"Total bytes on air:  {mesh.stats.bytes_on_air} "
          f"({mesh.stats.packets_sent} packets, {mesh.stats.packets_lost} lost)")
    print(f"Resends:             {stats.resends} data pieces, {stats.reasks} polls asked again, "
          f"{stats.duplicates} duplicates ignored")
    print(f"Simulated seconds:   {mesh.clock.now:.3f}")


if __name__ == "__main__":
    main()
