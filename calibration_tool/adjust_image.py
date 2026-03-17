#!/usr/bin/env python3
"""
adjust_image.py — Apply a correction profile to an image or PDF for print preparation.

Usage:
  python adjust_image.py --input photo.jpg    --output photo_print.png  --profile corrections.json [--debug]
  python adjust_image.py --input document.pdf --output document_out.pdf --profile corrections.json [--debug] [--dpi 300]

PDF notes:
  - Each page is rendered to a bitmap at --dpi (default 300), corrections applied,
    then all pages are repacked into a new PDF.
  - The output is a raster PDF — correct for photo-heavy print-ready files.
  - --debug appends the correction strip to the last page of the output PDF.
  - Requires pymupdf:  pip install pymupdf

Profile JSON keys (all optional — omit or set to neutral value for no change):

  brightness    float  Additive offset, all channels.        Range  -255 ... +255   (neutral: 0)
  contrast      float  Multiplier around midpoint 128.       Range   0.0 ... 3.0    (neutral: 1.0)
  saturation    float  HSV saturation multiplier.            Range   0.0 ... 3.0    (neutral: 1.0)
  hue_shift     float  Hue rotation in degrees.              Range  -180 ... +180   (neutral: 0)

  shadows       float  Additive lift/push, tones 0-85.       Range  -128 ... +128   (neutral: 0)
  midtones      float  Additive lift/push, tones 86-170.     Range  -128 ... +128   (neutral: 0)
  highlights    float  Additive lift/push, tones 171-255.    Range  -128 ... +128   (neutral: 0)

  r_offset      float  Red channel additive offset.          Range  -255 ... +255   (neutral: 0)
  g_offset      float  Green channel additive offset.        Range  -255 ... +255   (neutral: 0)
  b_offset      float  Blue channel additive offset.         Range  -255 ... +255   (neutral: 0)

  r_gain        float  Red channel gain multiplier.          Range   0.0 ... 3.0    (neutral: 1.0)
  g_gain        float  Green channel gain multiplier.        Range   0.0 ... 3.0    (neutral: 1.0)
  b_gain        float  Blue channel gain multiplier.         Range   0.0 ... 3.0    (neutral: 1.0)

Requirements:
  pip install Pillow numpy pymupdf
"""

import argparse
import io
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Missing dependencies.  Run:  pip install Pillow numpy pymupdf")
    sys.exit(1)

# pymupdf imported lazily — only when a PDF input is detected
fitz = None  # type: ignore


def _require_fitz() -> None:
    global fitz
    if fitz is None:
        try:
            import fitz as _fitz  # type: ignore
            fitz = _fitz
        except ImportError:
            print("[error]  PDF support requires pymupdf.  Run:  pip install pymupdf")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Defaults
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
    Order: channel gains -> channel offsets -> brightness -> contrast
           -> zone curve (shadows/midtones/highlights) -> saturation -> hue shift.
    Returns a new RGB image.
    """
    arr = np.array(img.convert("RGB"), dtype=np.float32)

    # 1. Per-channel gain
    arr[:, :, 0] *= p["r_gain"]
    arr[:, :, 1] *= p["g_gain"]
    arr[:, :, 2] *= p["b_gain"]

    # 2. Per-channel offset
    arr[:, :, 0] += p["r_offset"]
    arr[:, :, 1] += p["g_offset"]
    arr[:, :, 2] += p["b_offset"]

    # 3. Brightness (global additive)
    arr += p["brightness"]

    # 4. Contrast (scale around midpoint 128)
    arr = (arr - 128.0) * p["contrast"] + 128.0

    # 5. Zone curve
    lut = _build_zone_lut(p["shadows"], p["midtones"], p["highlights"])
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    arr = lut[arr]
    arr = arr.astype(np.float32)

    # 6. Saturation
    if p["saturation"] != 1.0:
        arr = np.clip(arr, 0, 255)
        arr = _apply_saturation(arr, p["saturation"])

    # 7. Hue shift
    if p["hue_shift"] != 0.0:
        arr = np.clip(arr, 0, 255)
        arr = _apply_hue_shift(arr, p["hue_shift"])

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _build_zone_lut(shadow: float, midtone: float, highlight: float) -> np.ndarray:
    x   = np.arange(256, dtype=np.float32)
    w_s = np.clip((85.0 - x) / 85.0,  0.0, 1.0)
    w_h = np.clip((x - 170.0) / 85.0, 0.0, 1.0)
    w_s = 0.5 * (1.0 + np.cos(np.pi * (1.0 - w_s)))
    w_h = 0.5 * (1.0 + np.cos(np.pi * (1.0 - w_h)))
    w_m = 1.0 - w_s - w_h
    adjustment = w_s * shadow + w_m * midtone + w_h * highlight
    return np.clip(x + adjustment, 0, 255).astype(np.uint8)


def _rgb_to_hsv(arr: np.ndarray) -> np.ndarray:
    r, g, b = arr[:, :, 0] / 255.0, arr[:, :, 1] / 255.0, arr[:, :, 2] / 255.0
    cmax  = np.maximum(np.maximum(r, g), b)
    cmin  = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin
    h = np.zeros_like(cmax)
    s = np.where(cmax > 0, delta / cmax, 0.0)
    mask_r = (delta > 0) & (cmax == r)
    mask_g = (delta > 0) & (cmax == g)
    mask_b = (delta > 0) & (cmax == b)
    h[mask_r] = 60.0 * (((g[mask_r] - b[mask_r]) / delta[mask_r]) % 6)
    h[mask_g] = 60.0 * (((b[mask_g] - r[mask_g]) / delta[mask_g]) + 2)
    h[mask_b] = 60.0 * (((r[mask_b] - g[mask_b]) / delta[mask_b]) + 4)
    return np.stack([h, s, cmax * 255.0], axis=-1)


def _hsv_to_rgb(hsv: np.ndarray) -> np.ndarray:
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2] / 255.0
    i = (h / 60.0).astype(np.int32) % 6
    f = h / 60.0 - np.floor(h / 60.0)
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    rgb = np.zeros((*h.shape, 3), dtype=np.float32)
    for idx, (rv, gv, bv) in enumerate([(v, t, p), (q, v, p), (p, v, t),
                                         (p, q, v), (t, p, v), (v, p, q)]):
        mask = i == idx
        rgb[:, :, 0][mask] = rv[mask]
        rgb[:, :, 1][mask] = gv[mask]
        rgb[:, :, 2][mask] = bv[mask]
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
    return abs(value - DEFAULTS[key]) > 1e-4


def build_debug_strip(img_width: int, profile: dict) -> Image.Image:
    """
    Return a white strip listing only parameters that differ from their
    neutral defaults.  Shows 'no corrections applied' if nothing changed.
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

    def _fmt(key: str, val: float) -> str:
        if key in ("brightness", "shadows", "midtones", "highlights",
                   "r_offset", "g_offset", "b_offset", "hue_shift"):
            return f"{val:+.0f}"
        return f"{val:.3f}"

    changed = [(k, profile[k]) for k in KEY_ORDER if _is_changed(k, profile[k])]
    tokens  = [f"{k}: {_fmt(k, v)}" for k, v in changed] if changed \
              else ["no corrections applied"]

    strip_h = PADDING + LINE_H + PADDING + LINE_H + PADDING
    strip   = Image.new("RGB", (img_width, strip_h), STRIP_BG)
    draw    = ImageDraw.Draw(strip)

    draw.line([(0, 0), (img_width - 1, 0)], fill=(200, 200, 200), width=1)
    draw.text((PADDING, PADDING), "Applied corrections:",
              fill=(120, 120, 120), font=font_b)

    x      = PADDING
    y      = PADDING + LINE_H + 2
    colour = CHANGED_COLOUR if changed else (140, 140, 140)
    for token in tokens:
        draw.text((x, y), token, fill=colour, font=font_s)
        x += len(token) * 7 + 18

    return strip


# ---------------------------------------------------------------------------
# PDF processing
# ---------------------------------------------------------------------------

def _pil_to_jpeg_bytes(img: Image.Image) -> bytes:
    """Encode a PIL image as JPEG bytes for embedding in a PDF page."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95, subsampling=0)
    return buf.getvalue()


def _page_size_pt(img: Image.Image, dpi: int) -> Tuple[float, float]:
    """Return (width_pt, height_pt) preserving physical dimensions at *dpi*."""
    return img.width * 72.0 / dpi, img.height * 72.0 / dpi


def process_pdf(
    input_path: str,
    output_path: str,
    profile: dict,
    dpi: int,
    debug: bool,
) -> None:
    _require_fitz()

    src = fitz.open(input_path)  # type: ignore
    n   = src.page_count
    print(f"[info]  PDF input:  {input_path}  ({n} page{'s' if n != 1 else ''})")

    out = fitz.open()  # type: ignore  — new empty PDF

    for page_num in range(n):
        page   = src.load_page(page_num)
        zoom   = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)  # type: ignore
        pix    = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)  # type: ignore

        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        print(f"[info]    Page {page_num + 1}/{n}  {img.width}x{img.height} px")

        corrected = apply_corrections(img, profile)

        # Attach debug strip to the last page only
        if debug and page_num == n - 1:
            strip  = build_debug_strip(corrected.width, profile)
            canvas = Image.new("RGB",
                               (corrected.width, corrected.height + strip.height),
                               STRIP_BG)
            canvas.paste(corrected, (0, 0))
            canvas.paste(strip, (0, corrected.height))
            corrected = canvas
            print("[info]    Debug strip appended to last page.")

        w_pt, h_pt = _page_size_pt(corrected, dpi)
        new_page   = out.new_page(width=w_pt, height=h_pt)
        new_page.insert_image(
            fitz.Rect(0, 0, w_pt, h_pt),  # type: ignore
            stream=_pil_to_jpeg_bytes(corrected),
        )

    changed = [k for k in profile if _is_changed(k, profile[k])]
    print(f"[info]  Corrections applied: {', '.join(changed) if changed else 'none'}")

    out.save(output_path, deflate=True, garbage=4)
    out.close()
    src.close()
    print(f"[info]  Output PDF: {output_path}  ({n} page{'s' if n != 1 else ''})")


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------

def process_image(
    input_path: str,
    output_path: str,
    profile: dict,
    debug: bool,
) -> None:
    img = Image.open(input_path).convert("RGB")
    print(f"[info]  Input:  {input_path}  {img.width}x{img.height} px")

    result = apply_corrections(img, profile)

    if debug:
        strip  = build_debug_strip(result.width, profile)
        canvas = Image.new("RGB",
                           (result.width, result.height + strip.height),
                           STRIP_BG)
        canvas.paste(result, (0, 0))
        canvas.paste(strip, (0, result.height))
        result = canvas
        print("[info]  Debug strip appended.")

    out         = Path(output_path)
    save_kwargs: dict = {"dpi": (300, 300)}
    if out.suffix.lower() in (".jpg", ".jpeg"):
        save_kwargs["quality"]     = 95
        save_kwargs["subsampling"] = 0
    result.save(str(out), **save_kwargs)

    changed = [k for k in profile if _is_changed(k, profile[k])]
    print(f"[info]  Corrections applied: {', '.join(changed) if changed else 'none'}")
    print(f"[info]  Output: {output_path}  {result.width}x{result.height} px")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply a correction profile to an image or PDF for print preparation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input",   required=True,
                        help="Input file: JPG, PNG, TIFF or PDF")
    parser.add_argument("--output",  required=True,
                        help="Output file")
    parser.add_argument("--profile", required=True,
                        help="Correction profile JSON")
    parser.add_argument("--debug",   action="store_true",
                        help="Append a correction annotation strip to the output")
    parser.add_argument("--dpi",     type=int, default=300,
                        help="Rasterisation DPI for PDF pages (default: 300)")

    args    = parser.parse_args()
    profile = load_profile(args.profile)

    if Path(args.input).suffix.lower() == ".pdf":
        if not args.output.lower().endswith(".pdf"):
            print(f"[warn]  PDF input detected but output extension is not .pdf: {args.output}")
        process_pdf(args.input, args.output, profile, args.dpi, args.debug)
    else:
        process_image(args.input, args.output, profile, args.debug)


if __name__ == "__main__":
    main()
