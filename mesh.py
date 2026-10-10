"""Mesh core: phones, links, loss, airtime, sim clock, routing. No real radios."""
import math
import random
import time
from collections import deque
from dataclasses import dataclass

# Meshtastic presets: spreading factor, bandwidth, coding rate (1 means 4/5, 4 means 4/8).
PRESETS = {
    "Short Turbo":   dict(sf=7,  bw_hz=500_000, cr=1),
    "Short Fast":    dict(sf=7,  bw_hz=250_000, cr=1),
    "Medium Fast":   dict(sf=9,  bw_hz=250_000, cr=1),
    "Long Fast":     dict(sf=11, bw_hz=250_000, cr=1),
    "Long Moderate": dict(sf=11, bw_hz=125_000, cr=4),
}


def bit_rate(preset):
    p = PRESETS[preset]
    return p["sf"] * p["bw_hz"] / 2 ** p["sf"] * 4 / (4 + p["cr"])


def airtime(packet_len, preset="Medium Fast", preamble_symbols=16):
    """Semtech LoRa time on air, in seconds. Explicit header, payload CRC on.
    Meshtastic uses 16 preamble symbols."""
    p = PRESETS[preset]
    sf, bw, cr = p["sf"], p["bw_hz"], p["cr"]
    t_sym = 2 ** sf / bw
    low_data_rate = 1 if t_sym > 0.016 else 0
    numerator = 8 * packet_len - 4 * sf + 28 + 16
    payload_symbols = 8 + max(math.ceil(numerator / (4 * (sf - 2 * low_data_rate))) * (cr + 4), 0)
    return (preamble_symbols + 4.25 + payload_symbols) * t_sym


class SimClock:
    """Simulated time in seconds. speed says how much faster than real time we run.
    If sleep is True, advance() also waits dt / speed real seconds."""

    def __init__(self, speed=10.0, sleep=False):
        self.now = 0.0
        self.speed = speed
        self.sleep = sleep

    def advance(self, dt):
        self.now += dt
        if self.sleep:
            time.sleep(dt / self.speed)


@dataclass
class Phone:
    id: str
    x: float
    y: float
    range: float = 250.0

    def distance_to(self, other):
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass
class AirStats:
    packets_sent: int = 0
    packets_lost: int = 0
    bytes_on_air: int = 0


class Link:
    """The transport seam. Every packet leaves a phone through send(packet_bytes).
    This simulated version waits for the airtime and rolls for loss.
    A real radio class would have the same method and write to a serial port."""

    def __init__(self, mesh, src, dst):
        self.mesh = mesh
        self.src = src
        self.dst = dst
        self.loss = mesh.loss_at(src.distance_to(dst))

    def send(self, packet_bytes):
        """Returns the bytes that arrive, or None if the packet was lost."""
        mesh = self.mesh
        mesh.clock.advance(airtime(len(packet_bytes), mesh.preset))
        mesh.stats.packets_sent += 1
        mesh.stats.bytes_on_air += len(packet_bytes)
        if mesh.rng.random() < self.loss:
            mesh.stats.packets_lost += 1
            return None
        if mesh.corrupt > 0 and mesh.rng.random() < mesh.corrupt:
            i = mesh.rng.randrange(len(packet_bytes))
            packet_bytes = packet_bytes[:i] + bytes([packet_bytes[i] ^ 0xFF]) + packet_bytes[i + 1:]
        return packet_bytes


class Mesh:
    """Five phones A to E in a line. Each reaches only its neighbors.
    loss is the loss on a link of one spacing. Loss grows with distance squared."""

    def __init__(self, seed=42, preset="Medium Fast", loss=0.20, corrupt=0.0,
                 spacing=200.0, radio_range=250.0, speed=10.0):
        self.rng = random.Random(seed)
        self.preset = preset
        self.corrupt = corrupt
        self.spacing = spacing
        self.loss_at_spacing = loss
        self.clock = SimClock(speed)
        self.stats = AirStats()
        self.phones = {
            name: Phone(name, 100 + i * spacing, 500, radio_range)
            for i, name in enumerate("ABCDE")
        }

    def loss_at(self, distance):
        return min(1.0, self.loss_at_spacing * (distance / self.spacing) ** 2)

    def in_range(self, a, b):
        d = a.distance_to(b)
        return a.id != b.id and d <= a.range and d <= b.range

    def neighbors(self, name):
        me = self.phones[name]
        return [p.id for p in self.phones.values() if self.in_range(me, p)]

    def link(self, src_name, dst_name):
        src, dst = self.phones[src_name], self.phones[dst_name]
        if not self.in_range(src, dst):
            raise ValueError(f"No link {src_name}->{dst_name}: out of range")
        return Link(self, src, dst)

    def route(self, src_name, dst_name):
        """Shortest path by hops (breadth first). Returns a list of names, or None."""
        queue = deque([[src_name]])
        seen = {src_name}
        while queue:
            path = queue.popleft()
            if path[-1] == dst_name:
                return path
            for n in sorted(self.neighbors(path[-1])):
                if n not in seen:
                    seen.add(n)
                    queue.append(path + [n])
        return None
