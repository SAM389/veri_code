"""Turn the hands photo into two small pixel maps for the hero (web/site/hands/).

Source: web/assets/hands-source.jpg, "Hand of a Person and a Bionic Hand" by cottonbro studio
(https://www.pexels.com/photo/6153343/, Pexels license). The photo itself is never served; the
page only gets these maps:

  robot.png / human.png   grayscale + alpha. Alpha = how much of the pixel is hand,
                          gray = brightness to draw it with (already normalized per hand).
  hands.json              map size, the photo-to-map factor, each crop's top edge in the photo
                          and each fingertip position (map pixels).

Run: python web/tools/make_hand_maps.py   (needs Pillow and NumPy: pip install -e ".[web-tools]")
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

WEB = Path(__file__).resolve().parent.parent
SRC = WEB / "assets" / "hands-source.jpg"
OUT = WEB / "site" / "hands"

FACTOR = 2  # photo pixels per map pixel
SPLIT_X = 848  # between the two fingertips, in photo pixels
CROPS = {"robot": (0, 960, SPLIT_X, 1440), "human": (SPLIT_X, 1140, 1600, 1780)}
THRESHOLD = 0.075  # color distance from the background that counts as "hand"
FADE_PX = 190  # fade where a hand is cut by the photo edge (the arm), in photo pixels


def hand_mask(rgb: np.ndarray) -> np.ndarray:
    bg = np.median(rgb.reshape(-1, 3), axis=0)
    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
    mask = Image.fromarray(((dist > THRESHOLD) * 255).astype(np.uint8))
    # close small holes, drop specks
    mask = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    mask = mask.filter(ImageFilter.MedianFilter(5))
    return _fill_holes(np.asarray(mask) > 127).astype(float)


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Background = what is reachable from a background corner; everything else is hand
    (fills bright skin highlights that matched the background color)."""
    # .copy(): floodfill silently does nothing on an image that shares memory with NumPy
    marked = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8)).copy()
    h, w = mask.shape
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if not mask[y, x]:
            ImageDraw.floodfill(marked, (x, y), 128)
    return np.asarray(marked) != 128


def shade(lum: np.ndarray, mask: np.ndarray, name: str) -> np.ndarray:
    vals = lum[mask > 0.5]
    lo, hi = np.percentile(vals, 3), np.percentile(vals, 99)
    v = np.clip((lum - lo) / max(hi - lo, 1e-6), 0, 1)
    if name == "robot":
        return 0.2 + 0.8 * v**0.75  # dark metal: lift the shadows so it reads on black
    return 0.12 + 0.88 * v**1.25  # skin: keep the soft shadows between fingers


def edge_boost(v: np.ndarray, alpha: np.ndarray, floor: float) -> np.ndarray:
    a = Image.fromarray((alpha * 255).astype(np.uint8))
    eroded = np.asarray(a.filter(ImageFilter.MinFilter(3))).astype(float) / 255
    edge = (alpha > 0.4) & (eroded < 0.4)
    out = v.copy()
    out[edge] = np.maximum(out[edge], floor)
    return out


def _shrink(values: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Area-average a 0..1 array down to `size` (w, h)."""
    img = Image.fromarray((values * 255).astype(np.uint8)).resize(size, Image.BOX)
    return np.asarray(img).astype(float) / 255


def build(img: Image.Image, name: str) -> dict:
    box = CROPS[name]
    crop = img.crop(box)
    rgb = np.asarray(crop).astype(float) / 255
    mask = hand_mask(rgb)
    lum = rgb @ np.array([0.299, 0.587, 0.114])

    # fade the arm where the photo cuts it off
    h, w = mask.shape
    ramp = np.ones(w)
    n = min(FADE_PX, w)
    if name == "robot":
        ramp[:n] = np.linspace(0, 1, n)
    else:
        ramp[-n:] = np.linspace(1, 0, n)
    mask = mask * ramp[None, :]

    v = shade(lum, mask, name)
    size = (w // FACTOR, h // FACTOR)
    small_a = _shrink(mask, size)
    small_v = _shrink(v * mask, size)
    small_v = np.where(small_a > 0, small_v / np.maximum(small_a, 1e-6), 0)
    small_v = edge_boost(np.clip(small_v, 0, 1), small_a, 0.7 if name == "robot" else 0.62)

    la = np.stack([small_v * 255, small_a * 255], axis=2).round().astype(np.uint8)
    OUT.mkdir(parents=True, exist_ok=True)
    Image.fromarray(la).save(OUT / f"{name}.png", optimize=True)  # H x W x 2 uint8 -> "LA"

    # fingertip: the hand pixel closest to the other hand
    ys, xs = np.nonzero(small_a > 0.5)
    i = xs.argmax() if name == "robot" else xs.argmin()
    return {"w": size[0], "h": size[1], "top": box[1], "tip": [int(xs[i]), int(ys[i])]}


def main() -> None:
    img = Image.open(SRC).convert("RGB")
    meta = {"factor": FACTOR, **{name: build(img, name) for name in CROPS}}
    (OUT / "hands.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
