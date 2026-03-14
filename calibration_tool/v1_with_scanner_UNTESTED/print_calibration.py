#!/usr/bin/env python3
"""
print_calibration.py — Print Calibration Target Generator & Corrector
----------------------------------------------------------------------
Two modes:

  generate  Build a calibration target PNG ready to send to the print shop.
            Embeds known patch values and a crop from your reference image.

  measure   After printing and scanning/photographing the result, feed the
            scanned image back in. The tool samples each patch, computes the
            deviation from the known target values, and writes a correction
            profile (JSON) plus an annotated deviation report image.

  apply     Apply a saved correction profile to any image to prepare it
            for printing on the same device/paper combination.

Usage examples:
  python print_calibration.py generate --ref photo.jpg --out target.png
  python print_calibration.py measure  --scan scanned_target.png --target target.png --out corrections.json
  python print_calibration.py apply    --image photo.jpg --corrections corrections.json --out photo_ready.png

Requirements:  pip3 install Pillow numpy
"""

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
except ImportError:
    print("Missing dependencies.  Run:  pip install Pillow numpy")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Patch definitions
# ---------------------------------------------------------------------------

# Greyscale ramp: 0 % → 100 % in 11 steps (0, 10, 20 … 100)
GREY_RAMP = [(v, v, v) for v in range(0, 256, 25)] + [(255, 255, 255)]

# Solid CMYK primaries expressed as RGB
COLOUR_PATCHES = {
    "Red":         (255,   0,   0),
    "Green":       (  0, 255,   0),
    "Blue":        (  0,   0, 255),
    "Cyan":        (  0, 255, 255),
    "Magenta":     (255,   0, 255),
    "Yellow":      (255, 255,   0),
    "White":       (255, 255, 255),
    "Black":       (  0,   0,   0),
    "Neutral 25%": ( 64,  64,  64),
    "Neutral 50%": (128, 128, 128),
    "Neutral 75%": (192, 192, 192),
    # Skin tones (D65 reference values)
    "Skin light":  (255, 224, 189),
    "Skin mid":    (224, 172, 105),
    "Skin dark":   (141,  85,  36),
    # Memory colours
    "Sky blue":    (135, 206, 235),
    "Grass green": ( 86, 130,  63),
    "Warm shadow": ( 72,  60,  50),
}

PATCH_SIZE   = 80   # px per patch square
LABEL_HEIGHT = 22   # px below each patch for the value label
MARGIN       = 40   # outer margin
COLUMN_GAP   = 12   # gap between patch columns
ROW_GAP      = 12   # gap between patch rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _try_font(size: int):
    """Return a truetype font at *size* pt, falling back to the PIL default."""
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


def _rgb_to_hex(r, g, b) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def _text_colour(bg: tuple) -> tuple:
    """Black or white label text depending on background luminance."""
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    return (0, 0, 0) if lum > 128 else (255, 255, 255)


def _delta_e(a: tuple, b: tuple) -> float:
    """Simple Euclidean distance in RGB — good enough for relative comparisons."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _sample_patch(img: Image.Image, x: int, y: int, size: int) -> tuple:
    """
    Return the median RGB of a *size*×*size* region starting at (x, y).
    Uses the centre 60 % of the patch to avoid edge contamination.
    """
    shrink = int(size * 0.20)
    region = img.crop((x + shrink, y + shrink,
                       x + size - shrink, y + size - shrink))
    arr = np.array(region.convert("RGB"))
    r = int(np.median(arr[:, :, 0]))
    g = int(np.median(arr[:, :, 1]))
    b = int(np.median(arr[:, :, 2]))
    return (r, g, b)


# ---------------------------------------------------------------------------
# Patch layout builder (shared between generate & measure)
# ---------------------------------------------------------------------------

def _build_patch_layout():
    """
    Return a list of dicts, each describing one patch:
      { 'label': str, 'target_rgb': tuple, 'col': int, 'row': int, 'section': str }
    """
    patches = []

    # Section 1 — greyscale ramp (single row)
    for i, rgb in enumerate(GREY_RAMP):
        v = rgb[0]
        patches.append({
            "label":      f"G{v}",
            "target_rgb": rgb,
            "col":        i,
            "row":        0,
            "section":    "grey",
        })

    # Section 2 — named colour patches (two columns of rows)
    items = list(COLOUR_PATCHES.items())
    cols  = 2
    for idx, (name, rgb) in enumerate(items):
        patches.append({
            "label":      name,
            "target_rgb": rgb,
            "col":        idx % cols,
            "row":        idx // cols,
            "section":    "colour",
        })

    return patches


def _section_origins(ref_crop_h: int):
    """
    Return pixel (x, y) origins for each section given a reference crop height.
    Layout (top → bottom):
      [margin]  greyscale row
      [gap]     colour grid
      [gap]     reference image crop
    """
    grey_y   = MARGIN
    colour_y = grey_y + PATCH_SIZE + LABEL_HEIGHT + ROW_GAP * 2
    crop_y   = colour_y + _colour_section_height() + ROW_GAP * 2
    return {
        "grey_y":   grey_y,
        "colour_y": colour_y,
        "crop_y":   crop_y,
    }


def _colour_section_height() -> int:
    items  = list(COLOUR_PATCHES.items())
    n_rows = math.ceil(len(items) / 2)
    return n_rows * (PATCH_SIZE + LABEL_HEIGHT) + (n_rows - 1) * ROW_GAP


def _grey_section_width() -> int:
    n = len(GREY_RAMP)
    return n * PATCH_SIZE + (n - 1) * COLUMN_GAP


def _colour_section_width() -> int:
    return 2 * PATCH_SIZE + COLUMN_GAP


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------

def cmd_generate(ref_path: str, out_path: str, crop_size: int = 300):
    ref_img = Image.open(ref_path).convert("RGB")

    # Take a central square crop from the reference image
    rw, rh  = ref_img.size
    cx, cy  = rw // 2, rh // 2
    half    = crop_size // 2
    crop    = ref_img.crop((cx - half, cy - half, cx + half, cy + half))
    crop    = crop.resize((crop_size, crop_size), Image.LANCZOS)

    origins   = _section_origins(crop_size)
    total_h   = origins["crop_y"] + crop_size + MARGIN
    grey_w    = _grey_section_width()
    colour_w  = _colour_section_width()
    total_w   = MARGIN + max(grey_w, colour_w, crop_size) + MARGIN

    canvas = Image.new("RGB", (total_w, total_h), (240, 240, 240))
    draw   = ImageDraw.Draw(canvas)
    font_s = _try_font(11)
    font_l = _try_font(13)

    # --- Section header helper ---
    def header(text, y):
        draw.text((MARGIN, y - 18), text, fill=(80, 80, 80), font=font_l)

    # --- Greyscale ramp ---
    header("Greyscale ramp  (known target values)", origins["grey_y"])
    for i, rgb in enumerate(GREY_RAMP):
        x = MARGIN + i * (PATCH_SIZE + COLUMN_GAP)
        y = origins["grey_y"]
        draw.rectangle([x, y, x + PATCH_SIZE, y + PATCH_SIZE], fill=rgb)
        draw.rectangle([x, y, x + PATCH_SIZE, y + PATCH_SIZE],
                       outline=(160, 160, 160), width=1)
        label = f"{rgb[0]}"
        tc    = _text_colour(rgb)
        draw.text((x + 4, y + PATCH_SIZE - 16), label, fill=tc, font=font_s)

    # --- Colour patches ---
    header("Colour patches  (known target values)", origins["colour_y"])
    items  = list(COLOUR_PATCHES.items())
    cols   = 2
    for idx, (name, rgb) in enumerate(items):
        col = idx % cols
        row = idx // cols
        x   = MARGIN + col * (PATCH_SIZE + COLUMN_GAP)
        y   = origins["colour_y"] + row * (PATCH_SIZE + LABEL_HEIGHT + ROW_GAP)
        draw.rectangle([x, y, x + PATCH_SIZE, y + PATCH_SIZE], fill=rgb)
        draw.rectangle([x, y, x + PATCH_SIZE, y + PATCH_SIZE],
                       outline=(160, 160, 160), width=1)
        tc = _text_colour(rgb)
        draw.text((x + 4, y + 6),  name,              fill=tc, font=font_s)
        draw.text((x + 4, y + 20), _rgb_to_hex(*rgb), fill=tc, font=font_s)

    # --- Reference crop ---
    header("Reference image crop  (centre sample)", origins["crop_y"])
    canvas.paste(crop, (MARGIN, origins["crop_y"]))
    draw.rectangle(
        [MARGIN, origins["crop_y"],
         MARGIN + crop_size, origins["crop_y"] + crop_size],
        outline=(100, 100, 100), width=2
    )

    # --- Metadata strip ---
    meta_y = origins["crop_y"] + crop_size + 8
    draw.text(
        (MARGIN, meta_y),
        f"Calibration target  |  ref: {Path(ref_path).name}  |  "
        f"crop centre ({cx},{cy})  |  patches: {len(GREY_RAMP)} grey + {len(COLOUR_PATCHES)} colour",
        fill=(100, 100, 100), font=font_s,
    )

    # Save metadata sidecar so measure mode can reconstruct layout
    meta = {
        "ref_path":  str(ref_path),
        "crop_size": crop_size,
        "crop_box":  [cx - half, cy - half, cx + half, cy + half],
        "canvas_size": [total_w, total_h],
    }
    meta_path = Path(out_path).with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2))

    canvas.save(out_path, dpi=(300, 300))
    print(f"[generate]  Target written to:  {out_path}")
    print(f"[generate]  Metadata sidecar:   {meta_path}")
    print(f"[generate]  Canvas size:        {total_w} × {total_h} px")
    print(f"[generate]  Patches:            {len(GREY_RAMP)} greyscale + {len(COLOUR_PATCHES)} colour")


# ---------------------------------------------------------------------------
# measure
# ---------------------------------------------------------------------------

def cmd_measure(scan_path: str, target_path: str, out_json: str,
                report_path: str | None = None):
    scan   = Image.open(scan_path).convert("RGB")
    target = Image.open(target_path).convert("RGB")

    # Resize scan to match target dimensions for coordinate alignment
    if scan.size != target.size:
        print(f"[measure]  Resizing scan {scan.size} → {target.size}")
        scan = scan.resize(target.size, Image.LANCZOS)

    origins = _section_origins(300)  # crop_size default; not used for sampling

    deviations = {}

    # Annotated report image
    report_img = scan.copy()
    draw       = ImageDraw.Draw(report_img)
    font_s     = _try_font(10)
    font_l     = _try_font(12)

    # --- Sample greyscale ramp ---
    for i, target_rgb in enumerate(GREY_RAMP):
        x = MARGIN + i * (PATCH_SIZE + COLUMN_GAP)
        y = origins["grey_y"]
        sampled = _sample_patch(scan, x, y, PATCH_SIZE)
        dE      = _delta_e(target_rgb, sampled)
        delta   = tuple(s - t for s, t in zip(sampled, target_rgb))
        key     = f"G{target_rgb[0]}"
        deviations[key] = {
            "section":    "grey",
            "target_rgb": target_rgb,
            "sampled_rgb": sampled,
            "delta_rgb":  delta,
            "delta_E":    round(dE, 2),
        }
        # Annotate on report
        _annotate_patch(draw, x, y, key, delta, dE, font_s)

    # --- Sample colour patches ---
    items = list(COLOUR_PATCHES.items())
    cols  = 2
    for idx, (name, target_rgb) in enumerate(items):
        col = idx % cols
        row = idx // cols
        x   = MARGIN + col * (PATCH_SIZE + COLUMN_GAP)
        y   = origins["colour_y"] + row * (PATCH_SIZE + LABEL_HEIGHT + ROW_GAP)
        sampled = _sample_patch(scan, x, y, PATCH_SIZE)
        dE      = _delta_e(target_rgb, sampled)
        delta   = tuple(s - t for s, t in zip(sampled, target_rgb))
        deviations[name] = {
            "section":    "colour",
            "target_rgb": target_rgb,
            "sampled_rgb": sampled,
            "delta_rgb":  delta,
            "delta_E":    round(dE, 2),
        }
        _annotate_patch(draw, x, y, name, delta, dE, font_s)

    # --- Derive global correction ---
    correction = _derive_correction(deviations)

    output = {
        "scan_path":   str(scan_path),
        "target_path": str(target_path),
        "deviations":  deviations,
        "correction":  correction,
    }
    Path(out_json).write_text(json.dumps(output, indent=2))
    print(f"[measure]  Corrections written to: {out_json}")

    # Print summary table
    _print_summary(deviations, correction)

    # Save annotated report
    rp = report_path or str(Path(out_json).with_name("deviation_report.png"))
    report_img.save(rp)
    print(f"[measure]  Deviation report:       {rp}")


def _annotate_patch(draw, x, y, label, delta, dE, font):
    """Overlay ΔE badge and per-channel deltas on a patch in the report image."""
    severity = (
        (200,  60,  60) if dE > 20 else
        (220, 140,   0) if dE > 10 else
        ( 40, 160,  40)
    )
    # Badge background
    draw.rectangle([x, y, x + PATCH_SIZE, y + 18], fill=severity)
    draw.text((x + 3, y + 3), f"ΔE {dE:.1f}", fill=(255, 255, 255), font=font)
    # Per-channel delta
    dr, dg, db = delta
    draw.text((x + 3, y + 20),
              f"R{dr:+d} G{dg:+d} B{db:+d}",
              fill=(240, 240, 40), font=font)


def _derive_correction(deviations: dict) -> dict:
    """
    Compute a simple global RGB correction from the measured deviations.

    Strategy:
      - Use neutral patches (greyscale + named neutrals) to derive the
        per-channel gain/offset correction that minimises overall error.
      - Output a 'curves' dict: per-channel shadow/midtone/highlight adjustments.
      - Also output overall brightness/contrast/saturation hints.
    """
    neutral_keys = [k for k in deviations
                    if deviations[k]["section"] == "grey" or
                    k in ("Neutral 25%", "Neutral 50%", "Neutral 75%", "White", "Black")]

    r_deltas, g_deltas, b_deltas = [], [], []
    for k in neutral_keys:
        d = deviations[k]["delta_rgb"]
        r_deltas.append(d[0])
        g_deltas.append(d[1])
        b_deltas.append(d[2])

    def _mean(lst): return round(sum(lst) / len(lst), 2) if lst else 0.0
    def _clamp(v, lo=-128, hi=128): return max(lo, min(hi, v))

    # Global channel offsets (negative = printer is pushing that channel high)
    r_off = _clamp(-_mean(r_deltas))
    g_off = _clamp(-_mean(g_deltas))
    b_off = _clamp(-_mean(b_deltas))

    grey_patches = [k for k in deviations if deviations[k]["section"] == "grey"]

    def _grey_val(k):
        return deviations[k]["target_rgb"][0]

    shadow_keys = [k for k in grey_patches if _grey_val(k) <= 64]
    mid_keys    = [k for k in grey_patches if 64 < _grey_val(k) < 192]
    hi_keys     = [k for k in grey_patches if _grey_val(k) >= 192]

    def zone_mean(keys):
        if not keys: return (0.0, 0.0, 0.0)
        rs = _mean([deviations[k]["delta_rgb"][0] for k in keys])
        gs = _mean([deviations[k]["delta_rgb"][1] for k in keys])
        bs = _mean([deviations[k]["delta_rgb"][2] for k in keys])
        return (round(-rs, 2), round(-gs, 2), round(-bs, 2))

    # Colour cast from non-neutral patches
    colour_deltas = [deviations[k]["delta_rgb"]
                     for k in deviations if deviations[k]["section"] == "colour"]
    sat_hint = ""
    if colour_deltas:
        avg_dE_colour = _mean([deviations[k]["delta_E"]
                               for k in deviations if deviations[k]["section"] == "colour"])
        avg_dE_grey   = _mean([deviations[k]["delta_E"]
                               for k in deviations if deviations[k]["section"] == "grey"])
        if avg_dE_colour > avg_dE_grey + 5:
            sat_hint = "Colour patches deviate more than neutrals — possible saturation compression; try +5 to +10 saturation boost before printing."
        elif avg_dE_grey > avg_dE_colour + 5:
            sat_hint = "Neutral patches deviate more — possible tonal shift; focus on curve adjustments."

    mid_patch = next((k for k in deviations
                      if deviations[k]["section"] == "grey" and
                      deviations[k]["target_rgb"][0] == 128), None)
    brightness_hint = ""
    if mid_patch:
        mid_delta = deviations[mid_patch]["delta_rgb"]
        if mid_delta[0] < -10:
            brightness_hint = f"Midtone appears dark by ~{abs(mid_delta[0])} levels — try +{abs(mid_delta[0])//4} brightness."
        elif mid_delta[0] > 10:
            brightness_hint = f"Midtone appears light by ~{mid_delta[0]} levels — try -{mid_delta[0]//4} brightness."

    return {
        "global_rgb_offset": {"R": r_off, "G": g_off, "B": b_off},
        "curves": {
            "shadows":    zone_mean(shadow_keys),
            "midtones":   zone_mean(mid_keys),
            "highlights": zone_mean(hi_keys),
        },
        "hints": {
            "saturation":  sat_hint or "Saturation deviation within normal range.",
            "brightness":  brightness_hint or "Midtone brightness within normal range.",
        }
    }


def _print_summary(deviations: dict, correction: dict):
    print("\n── Deviation summary ──────────────────────────────────────────")
    print(f"  {'Patch':<18}  {'Target':>10}  {'Sampled':>10}  {'Delta RGB':>14}  {'ΔE':>6}")
    print("  " + "─" * 64)
    for k, v in deviations.items():
        t  = v["target_rgb"]
        s  = v["sampled_rgb"]
        d  = v["delta_rgb"]
        dE = v["delta_E"]
        flag = "  ●" if dE > 20 else (" ○" if dE > 10 else "  ")
        print(f"  {k:<18}  {_rgb_to_hex(*t):>10}  {_rgb_to_hex(*s):>10}  "
              f"({d[0]:+4d},{d[1]:+4d},{d[2]:+4d})  {dE:>6.1f}{flag}")
    print()
    c = correction["global_rgb_offset"]
    print(f"  Global offset correction:  R{c['R']:+.0f}  G{c['G']:+.0f}  B{c['B']:+.0f}")
    print(f"  Saturation:  {correction['hints']['saturation']}")
    print(f"  Brightness:  {correction['hints']['brightness']}")
    print("────────────────────────────────────────────────────────────────\n")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def cmd_apply(image_path: str, corrections_json: str, out_path: str):
    data       = json.loads(Path(corrections_json).read_text())
    correction = data["correction"]
    img        = Image.open(image_path).convert("RGB")
    arr        = np.array(img, dtype=np.int32)

    print(f"[apply]  Input:       {image_path}  {img.size}")
    print(f"[apply]  Corrections: {corrections_json}")

    # 1. Per-channel global offset
    offset = correction["global_rgb_offset"]
    arr[:, :, 0] = np.clip(arr[:, :, 0] + offset["R"], 0, 255)
    arr[:, :, 1] = np.clip(arr[:, :, 1] + offset["G"], 0, 255)
    arr[:, :, 2] = np.clip(arr[:, :, 2] + offset["B"], 0, 255)
    print(f"[apply]  Channel offsets applied:  R{offset['R']:+.0f} G{offset['G']:+.0f} B{offset['B']:+.0f}")

    # 2. Zone-based curve correction (shadow / midtone / highlight)
    curves = correction["curves"]
    lut    = _build_curve_lut(curves["shadows"], curves["midtones"], curves["highlights"])
    arr    = _apply_lut(arr, lut)
    print(f"[apply]  Zone curve LUT applied")

    # 3. Saturation hint — auto-apply mild boost if indicated
    sat_hint = correction["hints"].get("saturation", "")
    sat_boost = 0.0
    if "boost" in sat_hint and "+5" in sat_hint:
        sat_boost = 1.05
    elif "boost" in sat_hint and "+10" in sat_hint:
        sat_boost = 1.10
    if sat_boost:
        result = Image.fromarray(arr.astype(np.uint8))
        result = ImageEnhance.Color(result).enhance(sat_boost)
        arr    = np.array(result, dtype=np.int32)
        print(f"[apply]  Saturation boost: ×{sat_boost:.2f}")

    result = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    result.save(out_path, dpi=(300, 300))
    print(f"[apply]  Corrected image: {out_path}")

    # Save a side-by-side comparison
    comp_path = str(Path(out_path).with_name(
        Path(out_path).stem + "_compare.png"))
    _save_comparison(Image.open(image_path).convert("RGB"), result, comp_path)
    print(f"[apply]  Comparison:      {comp_path}")


def _build_curve_lut(shadows, midtones, highlights) -> np.ndarray:
    """
    Build a 256-entry per-channel LUT from three zone corrections.
    Each zone tuple is (r_adj, g_adj, b_adj).
    Blend zones with a smooth ramp.
    """
    lut = np.zeros((256, 3), dtype=np.float32)
    x   = np.arange(256, dtype=np.float32)

    # Blend weights: shadow 0-85, midtone 86-170, highlight 171-255
    w_shadow    = np.clip(1.0 - x / 85.0,  0, 1)
    w_highlight = np.clip((x - 170) / 85.0, 0, 1)
    w_midtone   = 1.0 - w_shadow - w_highlight

    for ch in range(3):
        s  = shadows[ch]    if len(shadows)    > ch else 0
        m  = midtones[ch]   if len(midtones)   > ch else 0
        h  = highlights[ch] if len(highlights) > ch else 0
        adjustment = w_shadow * s + w_midtone * m + w_highlight * h
        lut[:, ch] = np.clip(x + adjustment, 0, 255)

    return lut.astype(np.uint8)


def _apply_lut(arr: np.ndarray, lut: np.ndarray) -> np.ndarray:
    out = arr.copy()
    for ch in range(3):
        out[:, :, ch] = lut[arr[:, :, ch], ch]
    return out


def _save_comparison(before: Image.Image, after: Image.Image, path: str,
                     max_w: int = 800):
    """Save a labelled before/after strip."""
    scale = min(1.0, max_w / before.width)
    nw    = int(before.width * scale)
    nh    = int(before.height * scale)
    b     = before.resize((nw, nh), Image.LANCZOS)
    a     = after.resize((nw, nh), Image.LANCZOS)

    bar_h  = 28
    canvas = Image.new("RGB", (nw * 2 + 4, nh + bar_h), (30, 30, 30))
    canvas.paste(b, (0,      bar_h))
    canvas.paste(a, (nw + 4, bar_h))
    draw  = ImageDraw.Draw(canvas)
    font  = _try_font(14)
    draw.text((6,      4), "Before", fill=(200, 200, 200), font=font)
    draw.text((nw + 10, 4), "After (corrected)", fill=(200, 200, 200), font=font)
    canvas.save(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Print calibration target generator and corrector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # generate
    g = sub.add_parser("generate", help="Build a calibration target sheet")
    g.add_argument("--ref",  required=True, help="Reference image (JPG/PNG/PDF preview)")
    g.add_argument("--out",  required=True, help="Output target PNG path")
    g.add_argument("--crop", type=int, default=300,
                   help="Side length (px) of reference crop (default 300)")

    # measure
    m = sub.add_parser("measure",
                       help="Sample scanned print, compute deviations & corrections")
    m.add_argument("--scan",    required=True, help="Scanned/photographed print")
    m.add_argument("--target",  required=True, help="Original target PNG (from generate)")
    m.add_argument("--out",     required=True, help="Output corrections JSON")
    m.add_argument("--report",  default=None,  help="Annotated deviation report PNG")

    # apply
    a = sub.add_parser("apply", help="Apply corrections to a print-ready image")
    a.add_argument("--image",       required=True, help="Source image to correct")
    a.add_argument("--corrections", required=True, help="Corrections JSON (from measure)")
    a.add_argument("--out",         required=True, help="Output corrected image")

    args = parser.parse_args()

    if args.cmd == "generate":
        cmd_generate(args.ref, args.out, args.crop)
    elif args.cmd == "measure":
        cmd_measure(args.scan, args.target, args.out, args.report)
    elif args.cmd == "apply":
        cmd_apply(args.image, args.corrections, args.out)


if __name__ == "__main__":
    main()
