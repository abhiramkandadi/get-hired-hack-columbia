"""Pieces, CRC, missing-piece bitmap, resends, final hash. Store and forward over the mesh.

Packet = 11 byte header + payload, 230 bytes at most.
Header: message id (4), type (1), piece number (2), total pieces (2), CRC-16 (2).
The CRC covers the header (without the CRC) and the payload.
"""
import binascii
import hashlib
import struct
from dataclasses import dataclass

MAX_PACKET = 230
HEADER = struct.Struct(">IBHHH")
PAYLOAD_MAX = MAX_PACKET - HEADER.size
DATA, POLL, BITMAP = 1, 2, 3
NAMES = {DATA: "DATA", POLL: "POLL", BITMAP: "BITMAP"}


def crc16(data):
    return binascii.crc_hqx(data, 0xFFFF)


def pack(msg_id, ptype, piece, total, payload=b""):
    if len(payload) > PAYLOAD_MAX:
        raise ValueError("payload too big")
    head = HEADER.pack(msg_id, ptype, piece, total, 0)
    return HEADER.pack(msg_id, ptype, piece, total, crc16(head[:-2] + payload)) + payload


def unpack(packet):
    """Returns (msg_id, type, piece, total, payload), or None if too short or the CRC is bad."""
    if len(packet) < HEADER.size:
        return None
    msg_id, ptype, piece, total, crc = HEADER.unpack(packet[:HEADER.size])
    payload = packet[HEADER.size:]
    if crc != crc16(packet[:HEADER.size - 2] + payload):
        return None
    return msg_id, ptype, piece, total, payload


def make_pieces(body):
    """The blob is SHA-256(body) + body. It is cut into pieces of PAYLOAD_MAX bytes."""
    blob = hashlib.sha256(body).digest() + body
    return [blob[i:i + PAYLOAD_MAX] for i in range(0, len(blob), PAYLOAD_MAX)]


def check_blob(blob):
    """Returns the body if the hash matches, else None."""
    digest, body = blob[:32], blob[32:]
    return body if hashlib.sha256(body).digest() == digest else None


def encode_bitmap(missing, total):
    """Bit i is 1 if piece i is missing."""
    bits = bytearray((total + 7) // 8)
    for i in missing:
        bits[i // 8] |= 1 << (i % 8)
    return bytes(bits)


def decode_bitmap(bits, total):
    return [i for i in range(total) if bits[i // 8] >> (i % 8) & 1]


@dataclass
class TransferStats:
    resends: int = 0       # DATA pieces sent again
    reasks: int = 0        # polls sent again after a timeout
    duplicates: int = 0    # extra copies a receiver ignored
    bad_crc: int = 0


class Receiver:
    """One phone collecting a message from the previous hop."""

    def __init__(self, msg_id, name, stats, log):
        self.msg_id = msg_id
        self.name = name
        self.stats = stats
        self.log = log
        self.pieces = {}
        self.total = None

    def missing(self):
        return [i for i in range(self.total) if i not in self.pieces]

    def complete(self):
        return self.total is not None and not self.missing()

    def blob(self):
        return b"".join(self.pieces[i] for i in range(self.total))

    def on_packet(self, packet):
        """Takes bytes off the air. Returns a reply packet, or None."""
        parsed = unpack(packet)
        if parsed is None:
            self.stats.bad_crc += 1
            self.log(f"  {self.name} bad CRC, packet dropped")
            return None
        msg_id, ptype, piece, total, payload = parsed
        if msg_id != self.msg_id:
            return None
        self.total = total
        if ptype == DATA:
            if piece in self.pieces:
                self.stats.duplicates += 1
                self.log(f"  {self.name} duplicate of piece {piece + 1}, ignored")
            else:
                self.pieces[piece] = payload
        elif ptype == POLL:
            return pack(self.msg_id, BITMAP, 0, total, encode_bitmap(self.missing(), total))
        return None


def send_hop(msg_id, pieces, fwd, rev, receiver, stats, log, ack_timeout=0.25, max_polls=60):
    """Send every piece over fwd, then poll for a bitmap and resend only what is missing.
    Returns True when the receiver holds all pieces. rev carries the bitmap back."""
    total = len(pieces)
    clock = fwd.mesh.clock
    hop = f"{fwd.src.id}->{fwd.dst.id}"
    sent_before = set()
    to_send = list(range(total))
    polls = 0
    while True:
        for i in to_send:
            resend = i in sent_before
            sent_before.add(i)
            stats.resends += resend
            got = fwd.send(pack(msg_id, DATA, i, total, pieces[i]))
            note = " (resend)" if resend else ""
            if got is None:
                log(f"{hop} DATA {i + 1}/{total}{note} LOST")
            else:
                receiver.on_packet(got)
                log(f"{hop} DATA {i + 1}/{total}{note} ok, {receiver.name} has {len(receiver.pieces)} of {total}")
        while True:
            got = fwd.send(pack(msg_id, POLL, 0, total))
            reply = receiver.on_packet(got) if got is not None else None
            back = rev.send(reply) if reply is not None else None
            parsed = unpack(back) if back is not None else None
            if parsed and parsed[1] == BITMAP:
                missing = decode_bitmap(parsed[4], total)
                log(f"{hop} bitmap back, missing: {[m + 1 for m in missing] or 'none'}")
                break
            what = "POLL lost" if got is None else ("BITMAP lost" if reply is not None else "POLL bad")
            polls += 1
            stats.reasks += 1
            log(f"{hop} {what}, timeout, asking again")
            clock.advance(ack_timeout)
            if polls >= max_polls:
                return False
        if not missing:
            return True
        to_send = missing


@dataclass
class Result:
    status: str
    delivered: bool
    body: bytes = b""


def send_message(mesh, src, dst, body, stats, log, msg_id=None):
    """Move body from src to dst, hop by hop. Delivered only if the final hash check passes."""
    msg_id = mesh.rng.getrandbits(32) if msg_id is None else msg_id
    pieces = make_pieces(body)
    log(f"Message {msg_id:08x}: {len(body)} bytes, {len(pieces)} pieces, status Queued")
    path = mesh.route(src, dst)
    if path is None:
        return Result("Stuck: no route", False)
    log(f"Route: {' -> '.join(path)}")
    for a, b in zip(path, path[1:]):
        log(f"Status: Sending {a} -> {b}")
        receiver = Receiver(msg_id, b, stats, log)
        if not send_hop(msg_id, pieces, mesh.link(a, b), mesh.link(b, a), receiver, stats, log):
            return Result(f"Stuck: no answer from {b} after repeated tries", False)
        log(f"Status: Checking at {b}")
        received = check_blob(receiver.blob())
        if received is None:
            return Result(f"Stuck: hash check failed at {b}", False)
        if b != dst:
            log(f"Status: At Phone {b} (holds all {len(pieces)} pieces, hash ok)")
    log(f"Status: Delivered (hash check passed at {dst})")
    return Result("Delivered", True, received)
