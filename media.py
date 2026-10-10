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


def _square(img, size):
    """Center crop to a square, then shrink. Every pixel comes from the photo."""
    return ImageOps.fit(img, (size, size), Image.LANCZOS)


def _webp(img, quality):
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()


def _closest_quality(img, target):
    """Try every quality from 1 to 100. Keep the one whose size is closest to target."""
    best = None
    for q in range(1, 101):
        data = _webp(img, q)
        if best is None or abs(len(data) - target) < abs(len(best[1]) - target):
            best = (q, data)
    return best


def make_layers(path):
    """Returns [layer 0 placeholder, layer 1, layer 2, layer 3]."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
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
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
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
