"""Photo layers. Layers 1 to 3 are WebP made from the real photo. Layer 0 is the AI caption."""
import io
import os
import sys
from dataclasses import dataclass

from PIL import Image, ImageOps

from transfer import make_pieces

# (layer, pixels, target bytes)
IMAGE_LAYERS = [(1, 32, 400), (2, 64, 1200), (3, 128, 3000)]


@dataclass
class Layer:
    index: int
    name: str
    pixels: int        # 0 for the caption
    quality: int       # WebP quality, 0 for the caption
    data: bytes        # empty until the caption is filled in


def _open(path, want):
    """Open a photo. A JPEG is decoded at a smaller scale, which is much faster for a 12 MP photo."""
    img = Image.open(path)
    img.draft("RGB", (want, want))
    return ImageOps.exif_transpose(img).convert("RGB")


def _square(img, size):
    """Center crop to a square, then shrink. Every pixel comes from the photo."""
    return ImageOps.fit(img, (size, size), Image.LANCZOS)


def _webp(img, quality):
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()


def _closest_quality(img, target):
    """Find the WebP quality whose size is closest to target. Size grows with quality,
    so binary search finds the area, then we check the qualities next to it."""
    cache = {}

    def size(q):
        if q not in cache:
            cache[q] = _webp(img, q)
        return cache[q]

    lo, hi = 1, 100
    while lo < hi:
        mid = (lo + hi) // 2
        if len(size(mid)) < target:
            lo = mid + 1
        else:
            hi = mid
    best = min(range(max(1, lo - 4), min(100, lo + 4) + 1), key=lambda q: abs(len(size(q)) - target))
    return best, size(best)


def make_layers(path):
    """Returns [layer 0 placeholder, layer 1, layer 2, layer 3]."""
    img = _open(path, 512)
    layers = [Layer(0, "caption", 0, 0, b"")]
    for index, pixels, target in IMAGE_LAYERS:
        quality, data = _closest_quality(_square(img, pixels), target)
        layers.append(Layer(index, f"WebP {pixels}x{pixels}", pixels, quality, data))
    return layers


def fill_caption(layers, text):
    layers[0].data = text.encode("utf-8")


def packets_needed(data):
    return len(make_pieces(data))


def for_ai(path, max_side=768):
    """JPEG copy of the photo, at most max_side pixels wide or tall. Used for the AI calls only."""
    img = _open(path, max_side)
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def print_table(layers, original_bytes):
    print(f"Original file: {original_bytes} bytes")
    print(f"{'Layer':<5} {'Name':<14} {'Pixels':<8} {'Quality':<8} {'Bytes':>6} {'Packets':>8}  vs original")
    for l in layers:
        pixels = f"{l.pixels}x{l.pixels}" if l.pixels else "text"
        quality = l.quality or "-"
        if l.data:
            packets = packets_needed(l.data)
            vs = f"{l.data and original_bytes / len(l.data):.0f}x smaller ({len(l.data) / original_bytes:.4%})"
        else:
            packets, vs = 1, "caption not made yet"
        print(f"{l.index:<5} {l.name:<14} {pixels:<8} {quality!s:<8} {len(l.data):>6} {packets:>8}  {vs}")


if __name__ == "__main__":
    path = sys.argv[1]
    layers = make_layers(path)
    print_table(layers, os.path.getsize(path))


# ---- Voice ----
import shutil
import subprocess

CODEC2_MODE = "700C"   # 700 bits per second, the smallest mode ffmpeg's libcodec2 offers


def _ffmpeg(args, data=None):
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise RuntimeError("ffmpeg not found")
    r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", *args],
                       input=data, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace").strip())
    return r.stdout


def _has_encoder(name):
    exe = shutil.which("ffmpeg")
    if exe is None:
        return False
    out = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True).stdout.decode(errors="replace")
    return any(line.split()[1:2] == [name] for line in out.splitlines())


def audio_seconds(path):
    out = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True).stdout.decode().strip()
    return float(out)


def encode_voice(path):
    """8 kHz mono, as small as possible. Codec2 first, else Opus at 6 kbps.
    Returns (bytes, codec name)."""
    base = ["-i", path, "-vn", "-ac", "1", "-ar", "8000"]
    if _has_encoder("libcodec2"):
        try:
            data = _ffmpeg([*base, "-c:a", "libcodec2", "-mode", CODEC2_MODE, "-f", "codec2", "pipe:1"])
            return data, f"Codec2 {CODEC2_MODE}"
        except RuntimeError as e:
            print(f"Codec2 failed ({e}). Using Opus.")
    data = _ffmpeg([*base, "-c:a", "libopus", "-b:a", "6k", "-application", "voip", "-f", "ogg", "pipe:1"])
    return data, "Opus 6 kbps"


def decode_voice(data):
    """Encoded voice back to WAV bytes, so we can play it."""
    return _ffmpeg(["-i", "pipe:0", "-f", "wav", "pipe:1"], data)


def voice_extension(codec):
    return ".c2" if codec.startswith("Codec2") else ".opus"


def print_voice_table(path, data, codec):
    original = os.path.getsize(path)
    print(f"Voice: {audio_seconds(path):.1f} s, original {original} bytes ({os.path.basename(path)})")
    print(f"Codec: {codec}. Encoded {len(data)} bytes ({len(data) * 8 / audio_seconds(path):.0f} bits per second), "
          f"{packets_needed(data)} packets, {original / len(data):.0f}x smaller than the original")
