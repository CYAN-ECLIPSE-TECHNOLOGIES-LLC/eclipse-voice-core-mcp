#!/usr/bin/env python3
"""Generate icon-512.png — brand §6: eclipse motif on navy
(#0d1b2a), one per-product glyph. Voice Core's glyph is the
consent ledger: a registry book with ruled lines and a proof
check, inside the gradient eclipse ring. PIL only; deterministic."""
from PIL import Image, ImageDraw

S = 512
NAVY = (13, 27, 42, 255)
STOPS = [(15, 246, 247), (0, 212, 255), (123, 44, 191)]


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)


def grad(t):
    return lerp(STOPS[0], STOPS[1], t * 2) if t < 0.5 else \
        lerp(STOPS[1], STOPS[2], (t - 0.5) * 2)


def main():
    img = Image.new("RGBA", (S, S), NAVY)
    d = ImageDraw.Draw(img)
    ring = [54, 54, 458, 458]
    for i in range(72):
        t = i / 72
        d.arc(ring, start=i * 5, end=i * 5 + 6, fill=grad(t), width=13)
    d.ellipse([126, 126, 386, 386], fill=NAVY)
    # The ledger: a registry book.
    book = [172, 128, 340, 384]
    d.rounded_rectangle(book, radius=12, fill=(8, 12, 20, 255),
                        outline=(254, 254, 254, 255), width=6)
    # Spine.
    d.line([(196, 140), (196, 372)], fill=grad(0.15), width=6)
    # Ruled consent lines.
    for i, y in enumerate((176, 220, 264)):
        d.line([(216, y), (316, y)], fill=grad(0.25 + 0.2 * i), width=9)
    # The proof check — consent on record.
    d.line([(222, 322), (248, 348)], fill=grad(0.6), width=11)
    d.line([(248, 348), (308, 292)], fill=grad(0.6), width=11)
    img.save("icon-512.png")
    print("wrote icon-512.png", img.size, img.mode)


if __name__ == "__main__":
    main()
