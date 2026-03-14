#!/usr/bin/env python3
"""
collage_generator3.py  —  v3
Identical to v2 except all layout/decoration parameters are expressed in
physical units (mm) in the INI instead of pixels.  The script converts
mm → px at startup using the configured DPI, so changing DPI is the only
edit needed to switch between preview (150 dpi) and print (300 dpi) quality.

Key change from v2:
  v2  INI uses pixel values tied to a specific DPI (e.g. target_row_height_px=420
      assumes 150 dpi; doubling DPI without changing this value halves the
      apparent row height on the output).
  v3  INI uses mm values (e.g. target_row_height_mm=71.0).  The script computes
      px = int(mm / 25.4 * dpi) so the physical layout is identical at any DPI.

Usage:
    python collage_generator3.py <folder> [output.pdf] [options]

Requirements:
    pip install Pillow reportlab
"""

import argparse
import configparser
import math
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageOps
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas


# ── Constants ─────────────────────────────────────────────────────────────────

MANIFEST_FILENAME = "order.txt"
SUPPORTED_EXTS    = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
ORDER_MODES       = ("filename", "shuffle", "manifest")
INF               = float("inf")


# ── Unit helpers ──────────────────────────────────────────────────────────────

def mm_to_px(mm: float, dpi: int) -> int:
    """Convert millimetres to pixels at the given DPI."""
    return max(1, int(mm / 25.4 * dpi))


def cm_to_px(cm_val: float, dpi: int) -> int:
    """Convert centimetres to pixels at the given DPI."""
    return max(1, int(cm_val / 2.54 * dpi))


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class Config:
    # [canvas]
    width_cm: float
    dpi: int
    background_color: Optional[tuple]
    transparent_background: bool

    # [layout] — stored in mm as read from INI
    order: str
    shuffle_seed: int
    target_row_height_mm: float
    min_row_height_mm: float
    max_row_height_mm: float
    min_images_per_row: int       # 0 = unconstrained
    max_images_per_row: int       # 0 = unconstrained
    padding_mm: float
    row_gap_mm: float

    # [dp_cost]
    scale_penalty_weight: float
    height_penalty_weight: float
    widows_penalty: float

    # [decoration] — stored in mm
    border_mm: float
    corner_radius_mm: float
    rotation_max_deg: float
    overlap_tolerance_mm: float

    # ── Derived pixel values (computed in load_config, not read from INI) ──
    # Stored here so the rest of the pipeline never needs to carry dpi around.
    target_row_height_px: int
    min_row_height_px: int
    max_row_height_px: int
    padding_px: int
    row_gap_px: int
    border_px: int
    corner_radius_px: int
    overlap_tolerance_px: int


DEFAULT_INI = Path(__file__).with_name("collage3.ini")


def load_config(ini_path: Path) -> Config:
    if not ini_path.exists():
        sys.exit(f"Config file not found: {ini_path}\n"
                 "Create it or pass --config <path>.")

    p = configparser.ConfigParser()
    p.read(ini_path)

    def _color(raw: str) -> tuple:
        parts = [int(x.strip()) for x in raw.split(",")]
        if len(parts) != 3:
            raise ValueError(f"Expected R,G,B — got: {raw!r}")
        return tuple(parts)

    canvas = p["canvas"]
    layout = p["layout"]
    dp     = p["dp_cost"]
    deco   = p["decoration"]

    transparent = canvas.getboolean("transparent_background", fallback=False)
    order = layout.get("order", "shuffle").strip().lower()
    if order not in ORDER_MODES:
        sys.exit(f"Invalid order={order!r}. Must be one of: {ORDER_MODES}")

    dpi = canvas.getint("dpi")

    # Read mm values
    target_mm   = layout.getfloat("target_row_height_mm")
    min_mm      = layout.getfloat("min_row_height_mm")
    max_mm      = layout.getfloat("max_row_height_mm")
    padding_mm  = layout.getfloat("padding_mm")
    row_gap_mm  = layout.getfloat("row_gap_mm")
    border_mm   = deco.getfloat("border_mm")
    radius_mm   = deco.getfloat("corner_radius_mm")
    overlap_mm  = deco.getfloat("overlap_tolerance_mm")

    return Config(
        width_cm               = canvas.getfloat("width_cm"),
        dpi                    = dpi,
        background_color       = None if transparent else _color(canvas["background_color"]),
        transparent_background = transparent,
        order                  = order,
        shuffle_seed           = layout.getint("shuffle_seed", fallback=42),
        target_row_height_mm   = target_mm,
        min_row_height_mm      = min_mm,
        max_row_height_mm      = max_mm,
        min_images_per_row     = layout.getint("min_images_per_row", fallback=0),
        max_images_per_row     = layout.getint("max_images_per_row", fallback=0),
        padding_mm             = padding_mm,
        row_gap_mm             = row_gap_mm,
        scale_penalty_weight   = dp.getfloat("scale_penalty_weight"),
        height_penalty_weight  = dp.getfloat("height_penalty_weight"),
        widows_penalty         = dp.getfloat("widows_penalty"),
        border_mm              = border_mm,
        corner_radius_mm       = radius_mm,
        rotation_max_deg       = deco.getfloat("rotation_max_deg"),
        overlap_tolerance_mm   = overlap_mm,
        # Derived px values — single conversion point
        target_row_height_px   = mm_to_px(target_mm,  dpi),
        min_row_height_px      = mm_to_px(min_mm,     dpi),
        max_row_height_px      = mm_to_px(max_mm,     dpi),
        padding_px             = mm_to_px(padding_mm, dpi),
        row_gap_px             = mm_to_px(row_gap_mm, dpi),
        border_px              = mm_to_px(border_mm,  dpi) if border_mm > 0 else 0,
        corner_radius_px       = mm_to_px(radius_mm,  dpi) if radius_mm > 0 else 0,
        overlap_tolerance_px   = mm_to_px(overlap_mm, dpi),
    )


# ── Ordering / manifest ───────────────────────────────────────────────────────

def discover_images(folder: Path) -> list:
    return [p for p in folder.iterdir() if p.suffix.lower() in SUPPORTED_EXTS]


def read_manifest(folder: Path) -> list:
    manifest = folder / MANIFEST_FILENAME
    if not manifest.exists():
        sys.exit(
            f"order.txt not found in {folder}.\n"
            "Run with --order shuffle or --order filename and --save-manifest "
            "to generate one."
        )
    lines = manifest.read_text(encoding="utf-8").splitlines()
    paths = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        p = folder / line
        if not p.exists():
            print(f"  ⚠  Manifest: file not found, skipping — {line}")
            continue
        paths.append(p)
    if not paths:
        sys.exit("order.txt exists but contains no valid image paths.")
    return paths


def resolve_order(folder: Path, cfg: Config,
                  cli_order: Optional[str], cli_seed: Optional[int]):
    if cli_order:
        mode = cli_order
    else:
        mode = cfg.order

    if mode == "filename" and (folder / MANIFEST_FILENAME).exists():
        print("  ℹ  order.txt found — switching to manifest mode automatically.\n"
              "     Pass --order filename to override.")
        mode = "manifest"

    seed: Optional[int] = None
    if mode == "shuffle":
        if cli_seed is not None:
            seed = cli_seed
        elif cfg.shuffle_seed != -1:
            seed = cfg.shuffle_seed

    if mode == "manifest":
        paths = read_manifest(folder)
        print(f"📋  Order: manifest ({len(paths)} images from order.txt)")
    elif mode == "shuffle":
        paths = sorted(discover_images(folder))
        rng = random.Random(seed)
        rng.shuffle(paths)
        print(f"🔀  Order: shuffle  (seed={seed if seed is not None else 'random'}, "
              f"{len(paths)} images)")
    else:
        paths = sorted(discover_images(folder))
        print(f"🔤  Order: filename ({len(paths)} images)")

    if not paths:
        sys.exit(f"No images found in {folder}")
    return paths, mode, seed


def write_manifest(folder: Path, paths: list, mode: str,
                   seed: Optional[int]) -> None:
    manifest = folder / MANIFEST_FILENAME
    ts        = datetime.now().strftime("%Y-%m-%d %H:%M")
    seed_note = f", seed={seed}" if seed is not None else ""
    header = (
        f"# order.txt — generated by collage_generator3.py\n"
        f"# Source mode : {mode}{seed_note}\n"
        f"# Generated   : {ts}\n"
        f"# Edit freely : lines starting with # are comments, blank lines ignored.\n"
        f"# To use: set  order = manifest  in collage3.ini\n"
        f"#\n"
    )
    manifest.write_text(header + "\n".join(p.name for p in paths) + "\n",
                        encoding="utf-8")
    print(f"💾  Manifest saved → {manifest}  ({len(paths)} entries)")


# ── Image decoration ──────────────────────────────────────────────────────────

def add_border(img: Image.Image, border_px: int) -> Image.Image:
    if border_px <= 0:
        return img
    return ImageOps.expand(img, border=border_px, fill=(255, 255, 255))


def round_corners(img: Image.Image, radius_px: int) -> Image.Image:
    if radius_px <= 0:
        return img
    img  = img.convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius_px, fill=255)
    img.putalpha(mask)
    return img


def _load_font(font_size: int):
    from PIL import ImageFont
    for candidate in ["DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf",
                      "Courier New Bold.ttf", "CourierNewBold.ttf",
                      "LiberationMono-Regular.ttf", "UbuntuMono-R.ttf"]:
        try:
            return ImageFont.truetype(candidate, font_size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _stamp_line(draw, text, font, y, img_w, fill=(255, 255, 255, 230)):
    bbox   = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text(((img_w - text_w) // 2, y), text, font=font, fill=fill)


def stamp_filename(img, name, scale=None):
    """
    Burn a debug label bar onto the BOTTOM of the image.

    Line 1: filename (white)
    Line 2: true scale = final display height / original file height
            colour-coded: green ±5%, yellow ±15%, red beyond ±15%
    """
    img  = img.convert("RGBA")
    w, h = img.size

    font   = _load_font(max(10, w // 14))
    dummy  = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    sample = dummy.textbbox((0, 0), "Mg", font=font)
    line_h = sample[3] - sample[1]
    pad_y  = max(3, line_h // 3)
    n_lines = 2 if scale is not None else 1
    bar_h  = n_lines * line_h + (n_lines + 1) * pad_y

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    draw.rectangle([(0, h - bar_h), (w, h)], fill=(0, 0, 0, 190))

    _stamp_line(draw, name, font, y=h - bar_h + pad_y, img_w=w)

    if scale is not None:
        dev = abs(scale - 1.0)
        color = (100, 230, 100, 230) if dev <= 0.05 else \
                (255, 210,  60, 230) if dev <= 0.15 else \
                (255,  80,  80, 230)
        _stamp_line(draw, f"x{scale:.3f}", font,
                    y=h - bar_h + pad_y + line_h + pad_y,
                    img_w=w, fill=color)

    return Image.alpha_composite(img, overlay)


# ── Loading ───────────────────────────────────────────────────────────────────

def load_images(paths: list, cfg: Config) -> list:
    """
    Load all images scaled to target_row_height_px (derived from
    target_row_height_mm × dpi at config load time).
    Stores _src_name and _orig_h on each image for the label pass.
    """
    target_h = cfg.target_row_height_px
    processed = []
    for p in paths:
        raw    = Image.open(p).convert("RGB")
        orig_h = raw.height
        ratio  = target_h / raw.height
        new_w  = max(1, int(raw.width * ratio))
        img    = raw.resize((new_w, target_h), Image.LANCZOS)
        img    = add_border(img, cfg.border_px)
        img    = round_corners(img, cfg.corner_radius_px)
        img._src_name = p.name
        img._orig_h   = orig_h
        processed.append(img)
    return processed


def stamp_scale_labels(rows: list) -> list:
    """
    Post-DP label pass — stamps filename + true scale on every image.
    Only called when --label-files is active.
    True scale = final display height / original file height.
    """
    stamped_rows = []
    for row in rows:
        stamped_row = []
        for img in row:
            name   = getattr(img, "_src_name", "?")
            orig_h = getattr(img, "_orig_h",   None)
            true_scale = (img.height / orig_h) if orig_h else None
            stamped = stamp_filename(img, name, scale=true_scale)
            stamped._src_name = name
            stamped._orig_h   = orig_h
            stamped_row.append(stamped)
        stamped_rows.append(stamped_row)
    return stamped_rows


# ── DP Row-breaking ───────────────────────────────────────────────────────────

def _row_scale(widths: list, canvas_w: int, padding_px: int) -> float:
    total_pad = padding_px * (len(widths) - 1)
    natural_w = sum(widths)
    if natural_w + total_pad <= 0:
        return INF
    return (canvas_w - total_pad) / natural_w


def _rot_expanded_width(w: int, h: int, max_rad: float) -> float:
    return w * math.cos(max_rad) + h * math.sin(max_rad)


def dp_break_rows(images: list, cfg: Config, canvas_w: int) -> list:
    """
    Knuth-Plass DP row-breaker.  All pixel values come from cfg derived fields
    (already converted from mm at load_config time).
    """
    n       = len(images)
    pad     = cfg.padding_px
    max_rad = math.radians(cfg.rotation_max_deg)
    t_h     = cfg.target_row_height_px
    sw      = cfg.scale_penalty_weight
    hw      = cfg.height_penalty_weight
    min_c   = cfg.min_images_per_row if cfg.min_images_per_row > 0 else 1
    max_c   = cfg.max_images_per_row if cfg.max_images_per_row > 0 else n

    rot_widths = [_rot_expanded_width(img.width, img.height, max_rad)
                  for img in images]

    cost  = [INF] * (n + 1)
    split = [-1]  * (n + 1)
    cost[0] = 0.0

    for i in range(1, n + 1):
        for j in range(max(0, i - max_c), i - min_c + 1):
            count = i - j
            if count < min_c or count > max_c:
                continue
            if cost[j] == INF:
                continue

            s = _row_scale(rot_widths[j:i], canvas_w, pad)
            if s <= 0 or s == INF:
                continue

            row_h = s * t_h
            if row_h < cfg.min_row_height_px or row_h > cfg.max_row_height_px:
                continue

            c = sw * (s - 1.0) ** 2 + hw * ((row_h - t_h) / t_h) ** 2
            if i == n and count == 1:
                c += cfg.widows_penalty

            total = cost[j] + c
            if total < cost[i]:
                cost[i]  = total
                split[i] = j

    if cost[n] == INF:
        print("  ⚠  DP: no solution within height bounds — falling back to greedy.")
        return _greedy_fallback(images, cfg, canvas_w)

    breaks = []
    i = n
    while i > 0:
        j = split[i]
        breaks.append((j, i))
        i = j
    breaks.reverse()

    rows = []
    for (j, i) in breaks:
        chunk = images[j:i]
        s     = _row_scale(rot_widths[j:i], canvas_w, pad)
        rows.append(_scale_row(chunk, s))
    return rows


def _greedy_fallback(images: list, cfg: Config, canvas_w: int) -> list:
    pad     = cfg.padding_px
    max_rad = math.radians(cfg.rotation_max_deg)

    def _rot_w(img):
        return img.width * math.cos(max_rad) + img.height * math.sin(max_rad)

    rows, current, cur_w = [], [], 0.0
    for img in images:
        rw    = _rot_w(img)
        extra = pad if current else 0
        if current and cur_w + extra + rw > canvas_w:
            s = (canvas_w - pad * (len(current) - 1)) / sum(_rot_w(x) for x in current)
            rows.append(_scale_row(current, s))
            current, cur_w = [img], rw
        else:
            current.append(img)
            cur_w += extra + rw
    if current:
        s = (canvas_w - pad * (len(current) - 1)) / sum(_rot_w(x) for x in current)
        rows.append(_scale_row(current, s))
    return rows


def _scale_row(chunk: list, s: float) -> list:
    """Resize all images in a chunk by scale factor s, preserving metadata."""
    scaled = []
    for img in chunk:
        new_w   = max(1, int(img.width  * s))
        new_h   = max(1, int(img.height * s))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        # PIL.Image.resize() drops custom attributes — copy explicitly
        resized._orig_h   = getattr(img, "_orig_h",   None)
        resized._src_name = getattr(img, "_src_name", "?")
        scaled.append(resized)
    return scaled


# ── Compositing ───────────────────────────────────────────────────────────────

def build_canvas(rows: list, canvas_w: int,
                 cfg: Config, rng: random.Random) -> Image.Image:
    mode = "RGBA" if cfg.transparent_background else "RGB"
    bg   = (0, 0, 0, 0) if cfg.transparent_background else cfg.background_color
    gap  = cfg.row_gap_px

    # Pass 1 — pre-rotate, measure total height
    pre: list = []
    total_h = gap
    for row in rows:
        angles  = [rng.uniform(-cfg.rotation_max_deg, cfg.rotation_max_deg)
                   for _ in row]
        rotated = [img.rotate(a, expand=True, resample=Image.BICUBIC)
                   for img, a in zip(row, angles)]
        row_h = max(r.height for r in rotated)
        pre.append((rotated, angles))
        total_h += row_h + gap

    # Pass 2 — composite using actual rotated widths
    canvas = Image.new(mode, (canvas_w, total_h), bg)
    y = gap
    for (rotated, _), _row in zip(pre, rows):
        row_h       = max(r.height for r in rotated)
        total_rot_w = sum(r.width for r in rotated)
        n_imgs      = len(rotated)
        total_gaps  = canvas_w - total_rot_w
        inter_gap   = max(cfg.overlap_tolerance_px, total_gaps // (n_imgs + 1))

        x = inter_gap
        for img_r in rotated:
            cy   = y + (row_h - img_r.height) // 2
            mask = img_r.split()[3] if img_r.mode == "RGBA" else None
            canvas.paste(img_r, (x, cy), mask)
            x += img_r.width + inter_gap
        y += row_h + gap

    return canvas


# ── Export ────────────────────────────────────────────────────────────────────

def save_pdf(collage: Image.Image, output_path: Path, cfg: Config) -> None:
    width_pt  = cfg.width_cm * cm
    height_pt = collage.height / collage.width * width_pt
    c   = rl_canvas.Canvas(str(output_path), pagesize=(width_pt, height_pt))
    tmp = output_path.with_suffix(".tmp.png")
    collage.save(tmp, "PNG")
    c.drawImage(str(tmp), 0, 0, width=width_pt, height=height_pt,
                mask="auto" if cfg.transparent_background else None)
    c.save()
    tmp.unlink(missing_ok=True)
    print(f"✓  {output_path}  "
          f"({cfg.width_cm}cm × {height_pt / cm:.1f}cm  |  "
          f"{collage.width}×{collage.height}px  @{cfg.dpi}dpi)")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Playful photo collage → PDF  [v3 — DPI-agnostic mm layout]",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("folder",  help="Folder containing source images")
    parser.add_argument("output",  nargs="?", default="collage.pdf",
                        help="Output PDF path")
    parser.add_argument("--config", type=Path, default=DEFAULT_INI,
                        help="Path to collage3.ini")
    parser.add_argument("--order",  choices=ORDER_MODES, default=None,
                        help="filename | shuffle | manifest  (overrides INI)")
    parser.add_argument("--seed",   type=int, default=None,
                        help="RNG seed for shuffle mode  (overrides INI)")
    parser.add_argument("--save-manifest", action="store_true",
                        help="Write resolved order to order.txt in source folder")
    parser.add_argument("--label-files",   action="store_true",
                        help="[DEBUG] Stamp filename + true scale on each image")
    args = parser.parse_args()

    cfg    = load_config(args.config)
    folder = Path(args.folder)
    if not folder.is_dir():
        sys.exit(f"Not a directory: {folder}")

    # Report derived pixel values so the user can verify DPI scaling
    print(f"⚙   DPI={cfg.dpi}  canvas={cfg.width_cm}cm × auto  "
          f"({cm_to_px(cfg.width_cm, cfg.dpi)}px wide)")
    print(f"    row height target={cfg.target_row_height_mm}mm "
          f"→ {cfg.target_row_height_px}px  "
          f"[{cfg.min_row_height_mm}–{cfg.max_row_height_mm}mm  "
          f"= {cfg.min_row_height_px}–{cfg.max_row_height_px}px]")
    print(f"    padding={cfg.padding_mm}mm={cfg.padding_px}px  "
          f"row_gap={cfg.row_gap_mm}mm={cfg.row_gap_px}px  "
          f"corners={cfg.corner_radius_mm}mm={cfg.corner_radius_px}px")

    paths, effective_mode, effective_seed = resolve_order(
        folder, cfg, args.order, args.seed
    )
    if args.save_manifest:
        write_manifest(folder, paths, effective_mode, effective_seed)

    canvas_w_px = cm_to_px(cfg.width_cm, cfg.dpi)
    layout_rng  = random.Random(effective_seed)

    print(f"🖼   Loading {len(paths)} images at "
          f"{cfg.target_row_height_mm}mm ({cfg.target_row_height_px}px) …")
    images = load_images(paths, cfg)

    print(f"🧮  DP row-breaking  "
          f"(target {cfg.target_row_height_px}px, "
          f"bounds [{cfg.min_row_height_px}–{cfg.max_row_height_px}px], "
          f"canvas {canvas_w_px}px) …")
    rows = dp_break_rows(images, cfg, canvas_w_px)

    # Layout diagnostics
    max_rad = math.radians(cfg.rotation_max_deg)
    scales, counts = [], []
    for row in rows:
        rw = sum(img.width * math.cos(max_rad) + img.height * math.sin(max_rad)
                 for img in row)
        pad_total = cfg.padding_px * (len(row) - 1)
        s = (canvas_w_px - pad_total) / rw if rw else 1.0
        scales.append(s)
        counts.append(len(row))
    print(f"    {len(rows)} rows  |  "
          f"imgs/row: {min(counts)}–{max(counts)}  |  "
          f"scale: {min(scales):.3f}–{max(scales):.3f}  "
          f"mean {sum(scales)/len(scales):.3f}  |  "
          f"rotation ±{cfg.rotation_max_deg}°")

    if args.label_files:
        rows = stamp_scale_labels(rows)
        print("    ⚠  DEBUG: labels stamped — not for final output")

    print("🎨  Compositing …")
    collage = build_canvas(rows, canvas_w_px, cfg, layout_rng)

    print("📄  Saving PDF …")
    save_pdf(collage, Path(args.output), cfg)


if __name__ == "__main__":
    main()
