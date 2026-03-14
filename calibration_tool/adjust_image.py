#!/usr/bin/env python3
"""
adjust_image.py — Apply a correction profile to an image for print preparation.

Usage:
  python adjust_image.py --input photo.jpg --output photo_print.png \
                         --profile corrections.json [--debug]

Profile JSON keys (all optional, omit or set to 0/1.0 for no change):

  brightness    float  Additive offset applied to all channels.  Range -255 … +255.
  contrast      float  Multiplier around the midpoint (128).     Range  0.0 … 3.0  (1.0 = no change)
  saturation    float  Colour saturation multiplier.             Range  0.0 … 3.0  (1.0 = no change)
  hue_shift     float  Hue rotation in degrees.                  Range -180 … +180

  shadows       float  Additive lift/push for tones 0–85.        Range -128 … +128
  midtones      float  Additive lift/push for tones 86–170.      Range -128 … +128
  highlights    float  Additive lift/push for tones 171–255.     Range -128 … +128

  r_offset      float  Per-channel red offset.                   Range -255 … +255
  g_offset      float  Per-channel green offset.                 Range -255 … +255
  b_offset      float  Per-channel blue offset.                  Range -255 … +255

  r_gain        float  Per-channel red gain (multiplier).        Range  0.0 … 3.0  (1.0 = no change)
  g_gain        float  Per-channel green gain (multiplier).      Range  0.0 … 3.0  (1.0 = no change)
  b_gain        float  Per-channel blue gain (multiplier).       Range  0.0 … 3.0  (1.0 = no change)

Example profile (corrections.json):
  {
    "brightness":  -10,
    "contrast":    1.05,
    "saturation":  1.10,
    "hue_shift":   0,
    "shadows":     8,
    "midtones":    0,
    "highlights":  -5,
    "r_offset":    -5,
    "g_offset":    4,
    "b_offset":    10,
    "r_gain":      1.0,
    "g_gain":      1.0,
    "b_gain":      1.0
  }

Requirements:  pip install Pillow numpy
"""

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Missing dependencies.  Run:  pip install Pillow numpy")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Defaults — every key has a neutral value so missing keys are safe
# ---------------------------------------------------------------------------

DEFAULTS = {
    "brightness":  0.0,
    "contrast":    1.0,
    "saturation":  1.0,
    "hue_shift":   0.0,
    "shadows":     0.0,
    "midtones":    0.0,
    "highlights":  0.0,
    "r_offset":    0.0,
    "g_offset":    0.0,
    "b_offset":    0.0,
    "r_gain":      1.0,
    "g_gain":      1.0,
    "b_gain":      1.0,
}


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

def load_profile(path: str) -> dict:
    raw = json.loads(Path(path).read_text())
    profile = dict(DEFAULTS)
    unknown = []
    for k, v in raw.items():
        if k in DEFAULTS:
            profile[k] = float(v)
        else:
            unknown.append(k)
    if unknown:
        print(f"[warn]  Unknown profile keys ignored: {unknown}")
    return profile


# ---------------------------------------------------------------------------
# Correction pipeline
# ---------------------------------------------------------------------------

def apply_corrections(img: Image.Image, p: dict) -> Image.Image:
    """
    Apply all corrections from profile *p* to *img*.
    Order: channel gains → channel offsets → brightness → contrast
           → shadows/midtones/highlights curve → saturation → hue shift.
    Returns a new RGB image.
    """
    arr = np.array(img.convert("RGB"), dtype=np.float32)

    # 1. Per-channel gain  (multiplicative around 0)
    arr[:, :, 0] = arr[:, :, 0] * p["r_gain"]
    arr[:, :, 1] = arr[:, :, 1] * p["g_gain"]
    arr[:, :, 2] = arr[:, :, 2] * p["b_gain"]

    # 2. Per-channel offset
    arr[:, :, 0] += p["r_offset"]
    arr[:, :, 1] += p["g_offset"]
    arr[:, :, 2] += p["b_offset"]

    # 3. Brightness (global additive)
    arr += p["brightness"]

    # 4. Contrast  (scale around midpoint 128)
    arr = (arr - 128.0) * p["contrast"] + 128.0

    # 5. Shadow / midtone / highlight zone curve
    #    Build a 256-entry LUT with smooth blending between zones.
    lut = _build_zone_lut(p["shadows"], p["midtones"], p["highlights"])
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    arr = lut[arr]   # vectorised LUT lookup

    arr = arr.astype(np.float32)

    # 6. Saturation  (convert to HSV, scale S, convert back)
    if p["saturation"] != 1.0:
        arr = np.clip(arr, 0, 255)
        arr = _apply_saturation(arr, p["saturation"])

    # 7. Hue shift  (convert to HSV, rotate H, convert back)
    if p["hue_shift"] != 0.0:
        arr = np.clip(arr, 0, 255)
        arr = _apply_hue_shift(arr, p["hue_shift"])

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _build_zone_lut(shadow: float, midtone: float, highlight: float) -> np.ndarray:
    """
    256-entry LUT.  Each output value is the input shifted by a weighted
    blend of the three zone corrections.
    """
    x   = np.arange(256, dtype=np.float32)
    # Smooth weight ramps using cosine (avoids hard transitions)
    w_s = np.clip((85.0  - x) / 85.0,  0.0, 1.0)
    w_h = np.clip((x - 170.0) / 85.0,  0.0, 1.0)
    w_s = 0.5 * (1.0 + np.cos(np.pi * (1.0 - w_s)))
    w_h = 0.5 * (1.0 + np.cos(np.pi * (1.0 - w_h)))
    w_m = 1.0 - w_s - w_h

    adjustment = w_s * shadow + w_m * midtone + w_h * highlight
    lut = np.clip(x + adjustment, 0, 255).astype(np.uint8)
    return lut


def _rgb_to_hsv(arr: np.ndarray) -> np.ndarray:
    """float32 RGB [0,255] → float32 HSV (H: 0–360, S: 0–1, V: 0–255)."""
    r, g, b = arr[:,:,0]/255.0, arr[:,:,1]/255.0, arr[:,:,2]/255.0
    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    h = np.zeros_like(cmax)
    s = np.where(cmax > 0, delta / cmax, 0.0)
    v = cmax

    mask_r = (delta > 0) & (cmax == r)
    mask_g = (delta > 0) & (cmax == g)
    mask_b = (delta > 0) & (cmax == b)

    h[mask_r] = 60.0 * (((g[mask_r] - b[mask_r]) / delta[mask_r]) % 6)
    h[mask_g] = 60.0 * (((b[mask_g] - r[mask_g]) / delta[mask_g]) + 2)
    h[mask_b] = 60.0 * (((r[mask_b] - g[mask_b]) / delta[mask_b]) + 4)

    return np.stack([h, s, v * 255.0], axis=-1)


def _hsv_to_rgb(hsv: np.ndarray) -> np.ndarray:
    """float32 HSV (H: 0–360, S: 0–1, V: 0–255) → float32 RGB [0,255]."""
    h, s, v = hsv[:,:,0], hsv[:,:,1], hsv[:,:,2] / 255.0
    i = (h / 60.0).astype(np.int32) % 6
    f = h / 60.0 - np.floor(h / 60.0)
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)

    rgb = np.zeros((*h.shape, 3), dtype=np.float32)
    for idx, (rv, gv, bv) in enumerate([(v,t,p),(q,v,p),(p,v,t),(p,q,v),(t,p,v),(v,p,q)]):
        mask = i == idx
        rgb[:,:,0][mask] = rv[mask]
        rgb[:,:,1][mask] = gv[mask]
        rgb[:,:,2][mask] = bv[mask]

    return rgb * 255.0


def _apply_saturation(arr: np.ndarray, factor: float) -> np.ndarray:
    hsv = _rgb_to_hsv(arr)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * factor, 0.0, 1.0)
    return _hsv_to_rgb(hsv)


def _apply_hue_shift(arr: np.ndarray, degrees: float) -> np.ndarray:
    hsv = _rgb_to_hsv(arr)
    hsv[:, :, 0] = (hsv[:, :, 0] + degrees) % 360.0
    return _hsv_to_rgb(hsv)


# ---------------------------------------------------------------------------
# Debug strip
# ---------------------------------------------------------------------------

STRIP_BG       = (255, 255, 255)
CHANGED_COLOUR = (180, 60, 60)


def _try_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/Library/Fonts/Courier New.ttf",
        "C:/Windows/Fonts/cour.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()


def _is_changed(key: str, value: float) -> bool:
    neutral = DEFAULTS[key]
    return abs(value - neutral) > 1e-4


def build_debug_strip(img_width: int, profile: dict) -> Image.Image:
    """
    Return a white strip listing only parameters that differ from their
    neutral defaults.  If nothing changed, shows a single note instead.
    """
    PADDING = 12
    LINE_H  = 14
    font_b  = _try_font(12)
    font_s  = _try_font(11)

    KEY_ORDER = [
        "brightness", "contrast",
        "shadows", "midtones", "highlights",
        "saturation", "hue_shift",
        "r_offset", "r_gain",
        "g_offset", "g_gain",
        "b_offset", "b_gain",
    ]

    def _fmt(key, val):
        if key in ("brightness", "shadows", "midtones", "highlights",
                   "r_offset", "g_offset", "b_offset", "hue_shift"):
            return f"{val:+.0f}"
        return f"{val:.3f}"

    changed = [(k, profile[k]) for k in KEY_ORDER if _is_changed(k, profile[k])]
    tokens  = [f"{k}: {_fmt(k, v)}" for k, v in changed] if changed               else ["no corrections applied"]

    strip_h = PADDING + LINE_H + PADDING + LINE_H + PADDING
    strip   = Image.new("RGB", (img_width, strip_h), STRIP_BG)
    draw    = ImageDraw.Draw(strip)

    draw.line([(0, 0), (img_width - 1, 0)], fill=(200, 200, 200), width=1)
    draw.text((PADDING, PADDING), "Applied corrections:",
              fill=(120, 120, 120), font=font_b)

    x   = PADDING
    y   = PADDING + LINE_H + 2
    gap = 18
    colour = CHANGED_COLOUR if changed else (140, 140, 140)
    for token in tokens:
        draw.text((x, y), token, fill=colour, font=font_s)
        x += len(token) * 7 + gap

    return strip


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Apply a JSON correction profile to an image for print preparation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input",   required=True,  help="Input image (JPG / PNG / TIFF)")
    parser.add_argument("--output",  required=True,  help="Output image path")
    parser.add_argument("--profile", required=True,  help="Correction profile JSON")
    parser.add_argument("--debug",   action="store_true",
                        help="Append a white annotation strip below the image")

    args = parser.parse_args()

    # Load
    profile = load_profile(args.profile)
    img     = Image.open(args.input).convert("RGB")
    print(f"[info]  Input:   {args.input}  {img.size[0]}×{img.size[1]} px")

    # Apply corrections
    result = apply_corrections(img, profile)

    # Debug strip
    if args.debug:
        strip  = build_debug_strip(result.width, profile)
        canvas = Image.new("RGB",
                           (result.width, result.height + strip.height),
                           STRIP_BG)
        canvas.paste(result, (0, 0))
        canvas.paste(strip,  (0, result.height))
        result = canvas
        print("[info]  Debug strip appended.")

    # Save
    out = Path(args.output)
    save_kwargs = {"dpi": (300, 300)}
    if out.suffix.lower() in (".jpg", ".jpeg"):
        save_kwargs["quality"]  = 95
        save_kwargs["subsampling"] = 0
    result.save(str(out), **save_kwargs)

    # Summary
    changed = [k for k in profile if _is_changed(k, profile[k])]
    print(f"[info]  Corrections applied: {', '.join(changed) if changed else 'none'}")
    print(f"[info]  Output:  {args.output}  {result.size[0]}×{result.size[1]} px")


if __name__ == "__main__":
    main()
