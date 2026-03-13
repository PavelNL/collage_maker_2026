#!/usr/bin/env python3
"""
collage_generator2.py  —  v2
Generates a playful photo collage PDF using a dynamic-programming row-breaking
algorithm (Option B) that minimises scale distortion across all rows.

Key difference from v1:
  v1 forces a fixed image-count per row, then rescales to fit — causing brutal
  up/down scaling when aspect ratios don't cooperate.
  v2 treats row-breaking like the Knuth-Plass paragraph justification algorithm:
  it finds the globally optimal set of row breaks that minimises the total
  squared deviation of each row's scale factor from 1.0.  Images are loaded at
  a reference height; the per-row correction is always small (typically < 5%).

All tuneable parameters live in collage2.ini.
Image ordering is identical to v1: filename / shuffle / manifest + --save-manifest.

Usage:
    python collage_generator2.py <folder> [output.pdf] [options]

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


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class Config:
    # [canvas]
    width_cm: float
    dpi: int
    background_color: Optional[tuple]
    transparent_background: bool

    # [layout]
    order: str
    shuffle_seed: int

    # DP layout parameters
    target_row_height_px: int   # reference height images are loaded at
    min_row_height_px: int      # hard lower bound on final row height
    max_row_height_px: int      # hard upper bound on final row height
    min_images_per_row: int     # optional count guard (0 = unconstrained)
    max_images_per_row: int     # optional count guard (0 = unconstrained)
    padding_px: int             # horizontal gap between images
    row_gap_px: int             # vertical gap between rows

    # DP cost weights
    scale_penalty_weight: float     # penalises deviation of row scale from 1.0
    height_penalty_weight: float    # penalises row height straying from target
    widows_penalty: float           # extra cost for a final row with 1 image

    # [decoration]
    border_px: int
    corner_radius_px: int
    rotation_max_deg: float
    overlap_tolerance_px: int


DEFAULT_INI = Path(__file__).with_name("collage2.ini")


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
    order = layout.get("order", "filename").strip().lower()
    if order not in ORDER_MODES:
        sys.exit(f"Invalid order={order!r}. Must be one of: {ORDER_MODES}")

    return Config(
        width_cm               = canvas.getfloat("width_cm"),
        dpi                    = canvas.getint("dpi"),
        background_color       = None if transparent else _color(canvas["background_color"]),
        transparent_background = transparent,
        order                  = order,
        shuffle_seed           = layout.getint("shuffle_seed", fallback=-1),
        target_row_height_px   = layout.getint("target_row_height_px"),
        min_row_height_px      = layout.getint("min_row_height_px"),
        max_row_height_px      = layout.getint("max_row_height_px"),
        min_images_per_row     = layout.getint("min_images_per_row", fallback=1),
        max_images_per_row     = layout.getint("max_images_per_row", fallback=0),
        padding_px             = layout.getint("padding_px"),
        row_gap_px             = layout.getint("row_gap_px"),
        scale_penalty_weight   = dp.getfloat("scale_penalty_weight"),
        height_penalty_weight  = dp.getfloat("height_penalty_weight"),
        widows_penalty         = dp.getfloat("widows_penalty"),
        border_px              = deco.getint("border_px"),
        corner_radius_px       = deco.getint("corner_radius_px"),
        rotation_max_deg       = deco.getfloat("rotation_max_deg"),
        overlap_tolerance_px   = deco.getint("overlap_tolerance_px", fallback=3),
    )


# ── Unit helpers ──────────────────────────────────────────────────────────────

def px(cm_val: float, dpi: int) -> int:
    return int(cm_val / 2.54 * dpi)


# ── Ordering / manifest  (identical to v1) ────────────────────────────────────

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
        f"# order.txt — generated by collage_generator2.py\n"
        f"# Source mode : {mode}{seed_note}\n"
        f"# Generated   : {ts}\n"
        f"# Edit freely : lines starting with # are comments, blank lines ignored.\n"
        f"# To use: set  order = manifest  in collage2.ini\n"
        f"#\n"
    )
    manifest.write_text(header + "\n".join(p.name for p in paths) + "\n",
                        encoding="utf-8")
    print(f"💾  Manifest saved → {manifest}  ({len(paths)} entries)")


# ── Image decoration  (identical to v1) ──────────────────────────────────────

def add_border(img: Image.Image, border: int) -> Image.Image:
    if border <= 0:
        return img
    return ImageOps.expand(img, border=border, fill=(255, 255, 255))


def round_corners(img: Image.Image, radius: int) -> Image.Image:
    if radius <= 0:
        return img
    img  = img.convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)
    img.putalpha(mask)
    return img


def stamp_filename(img: Image.Image, name: str) -> Image.Image:
    """Burn filename as a semi-transparent label bar (debug mode)."""
    from PIL import ImageFont
    img  = img.convert("RGBA")
    w, h = img.size
    font_size  = max(10, w // 14)
    font = None
    for candidate in ["DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf",
                      "Courier New Bold.ttf", "CourierNewBold.ttf",
                      "LiberationMono-Regular.ttf", "UbuntuMono-R.ttf"]:
        try:
            font = ImageFont.truetype(candidate, font_size)
            break
        except (IOError, OSError):
            continue
    if font is None:
        font = ImageFont.load_default()

    dummy  = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox   = dummy.textbbox((0, 0), name, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_y  = max(3, text_h // 3)
    bar_h  = text_h + pad_y * 2

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    draw.rectangle([(0, h - bar_h), (w, h)], fill=(0, 0, 0, 180))
    draw.text(((w - text_w) // 2, h - bar_h + pad_y),
              name, font=font, fill=(255, 255, 255, 230))
    return Image.alpha_composite(img, overlay)


# ── Loading ───────────────────────────────────────────────────────────────────

def load_images(paths: list, cfg: Config, label_filenames: bool = False) -> list:
    """
    Load all images scaled to target_row_height_px.
    Decoration (border, corners) is applied here at reference height.
    The DP solver then scales rows by small correction factors (≈1.0).
    """
    target_h  = cfg.target_row_height_px
    processed = []
    for p in paths:
        img   = Image.open(p).convert("RGB")
        ratio = target_h / img.height
        new_w = max(1, int(img.width * ratio))
        img   = img.resize((new_w, target_h), Image.LANCZOS)
        img   = add_border(img, cfg.border_px)
        img   = round_corners(img, cfg.corner_radius_px)
        if label_filenames:
            img = stamp_filename(img, p.name)
        processed.append(img)
    return processed


# ── DP Row-breaking ───────────────────────────────────────────────────────────

def _row_scale(widths: list, canvas_w: int, padding: int) -> float:
    """
    The scale factor needed to make a row of images (with given widths) fill
    exactly canvas_w pixels with `padding` between each pair.
    Returns INF if the row is infeasible (count constraints violated).
    """
    n         = len(widths)
    total_pad = padding * (n - 1)
    natural_w = sum(widths)
    if natural_w + total_pad <= 0:
        return INF
    return (canvas_w - total_pad) / natural_w


def _rot_expanded_width(w: int, h: int, max_rad: float) -> float:
    """Worst-case rotated bounding-box width."""
    return w * math.cos(max_rad) + h * math.sin(max_rad)


def dp_break_rows(images: list, cfg: Config, canvas_w: int) -> list:
    """
    Knuth-Plass style dynamic programming row-breaker.

    State:  cost[i] = minimum total cost to lay out images[0..i-1]
    Transition: for each j < i, consider placing images[j..i-1] in one row.
      - Compute the scale factor s needed to fill canvas_w.
      - Reject if s would push row height outside [min, max] bounds.
      - Reject if count constraints (min/max_images_per_row) are violated.
      - Cost contribution = scale_penalty * (s-1)^2
                          + height_penalty * ((s*target_h - target_h)/target_h)^2
      - Add widows_penalty if this is the last row and contains only 1 image.
    Backtrack from cost[N] to recover the row break indices.

    Rotation is accounted for in the effective width used for scale computation,
    identical to v1's worst-case approach — so the small correction scale never
    causes overlap.
    """
    n       = len(images)
    pad     = cfg.padding_px
    max_rad = math.radians(cfg.rotation_max_deg)
    t_h     = cfg.target_row_height_px
    sw      = cfg.scale_penalty_weight
    hw      = cfg.height_penalty_weight
    min_c   = cfg.min_images_per_row if cfg.min_images_per_row > 0 else 1
    max_c   = cfg.max_images_per_row if cfg.max_images_per_row > 0 else n

    # Pre-compute worst-case rotated widths (at reference height)
    rot_widths = [
        _rot_expanded_width(img.width, img.height, max_rad)
        for img in images
    ]

    # cost[i]  = min cost to place images[0..i-1]
    # split[i] = j such that images[j..i-1] form the last row in optimal solution
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

            chunk_rot_w = rot_widths[j:i]
            s = _row_scale(chunk_rot_w, canvas_w, pad)

            if s <= 0 or s == INF:
                continue

            # Enforce height bounds
            row_h = s * t_h
            if row_h < cfg.min_row_height_px or row_h > cfg.max_row_height_px:
                continue

            # Cost: squared scale deviation + squared relative height deviation
            c = sw * (s - 1.0) ** 2 + hw * ((row_h - t_h) / t_h) ** 2

            # Widows penalty: last row (i == n) with a single image
            if i == n and count == 1:
                c += cfg.widows_penalty

            total = cost[j] + c
            if total < cost[i]:
                cost[i]  = total
                split[i] = j

    # ── Backtrack ──────────────────────────────────────────────────────────
    if cost[n] == INF:
        # Fallback: no valid solution found within constraints — relax height
        # bounds and retry with a single greedy row per image group.
        print("  ⚠  DP: no solution within height bounds — falling back to "
              "unconstrained greedy layout.")
        return _greedy_fallback(images, cfg, canvas_w)

    breaks = []
    i = n
    while i > 0:
        j = split[i]
        breaks.append((j, i))
        i = j
    breaks.reverse()

    # ── Scale and resize each row ──────────────────────────────────────────
    rows = []
    for (j, i) in breaks:
        chunk      = images[j:i]
        chunk_rw   = rot_widths[j:i]
        s          = _row_scale(chunk_rw, canvas_w, pad)
        scaled_row = []
        for img in chunk:
            new_w = max(1, int(img.width  * s))
            new_h = max(1, int(img.height * s))
            scaled_row.append(img.resize((new_w, new_h), Image.LANCZOS))
        rows.append(scaled_row)

    return rows


def _greedy_fallback(images: list, cfg: Config, canvas_w: int) -> list:
    """
    Simple greedy width-based row filling used as DP fallback.
    Fills rows until adding the next image would exceed canvas_w,
    then scales the completed row to fit exactly.
    """
    pad     = cfg.padding_px
    max_rad = math.radians(cfg.rotation_max_deg)

    def _rot_w(img: Image.Image) -> float:
        return img.width * math.cos(max_rad) + img.height * math.sin(max_rad)

    rows, current, cur_w = [], [], 0.0
    for img in images:
        rw = _rot_w(img)
        extra = pad if current else 0
        if current and cur_w + extra + rw > canvas_w:
            rows.append(_scale_row(current, canvas_w, pad, max_rad))
            current, cur_w = [img], rw
        else:
            current.append(img)
            cur_w += extra + rw
    if current:
        rows.append(_scale_row(current, canvas_w, pad, max_rad))
    return rows


def _scale_row(chunk: list, canvas_w: int, pad: int, max_rad: float) -> list:
    rot_ws = [img.width * math.cos(max_rad) + img.height * math.sin(max_rad)
              for img in chunk]
    total_pad = pad * (len(chunk) - 1)
    s = (canvas_w - total_pad) / sum(rot_ws) if sum(rot_ws) else 1.0
    scaled = []
    for img in chunk:
        new_w = max(1, int(img.width  * s))
        new_h = max(1, int(img.height * s))
        scaled.append(img.resize((new_w, new_h), Image.LANCZOS))
    return scaled


# ── Compositing  (identical to v1) ───────────────────────────────────────────

def build_canvas(rows: list, canvas_w: int,
                 cfg: Config, rng: random.Random) -> Image.Image:
    mode = "RGBA" if cfg.transparent_background else "RGB"
    bg   = (0, 0, 0, 0) if cfg.transparent_background else cfg.background_color
    gap  = cfg.row_gap_px

    # Pass 1 — pre-rotate, measure height
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


# ── Export  (identical to v1) ─────────────────────────────────────────────────

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
          f"{collage.width}×{collage.height}px)")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Playful photo collage → PDF  [v2 — DP layout]",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("folder",  help="Folder containing source images")
    parser.add_argument("output",  nargs="?", default="collage.pdf",
                        help="Output PDF path")
    parser.add_argument("--config", type=Path, default=DEFAULT_INI,
                        help="Path to collage2.ini")
    parser.add_argument("--order",  choices=ORDER_MODES, default=None,
                        help="filename | shuffle | manifest  (overrides INI)")
    parser.add_argument("--seed",   type=int, default=None,
                        help="RNG seed for shuffle mode")
    parser.add_argument("--save-manifest", action="store_true",
                        help="Write resolved order to order.txt in source folder")
    parser.add_argument("--label-files",   action="store_true",
                        help="[DEBUG] Stamp filename on each image")
    args = parser.parse_args()

    cfg    = load_config(args.config)
    folder = Path(args.folder)
    if not folder.is_dir():
        sys.exit(f"Not a directory: {folder}")

    paths, effective_mode, effective_seed = resolve_order(
        folder, cfg, args.order, args.seed
    )
    if args.save_manifest:
        write_manifest(folder, paths, effective_mode, effective_seed)

    canvas_w_px = px(cfg.width_cm, cfg.dpi)
    layout_rng  = random.Random(effective_seed)

    print(f"🖼   Loading {len(paths)} images at {cfg.target_row_height_px}px "
          f"reference height …")
    images = load_images(paths, cfg, label_filenames=args.label_files)
    if args.label_files:
        print("    ⚠  DEBUG: filenames stamped — not for final output")

    print(f"🧮  DP row-breaking  "
          f"(target {cfg.target_row_height_px}px, "
          f"bounds [{cfg.min_row_height_px}–{cfg.max_row_height_px}px], "
          f"canvas {canvas_w_px}px) …")
    rows = dp_break_rows(images, cfg, canvas_w_px)

    # ── Print layout diagnostics ──────────────────────────────────────────
    scales = []
    max_rad = math.radians(cfg.rotation_max_deg)
    for row in rows:
        rw = sum(img.width * math.cos(max_rad) + img.height * math.sin(max_rad)
                 for img in row)
        pad_total = cfg.padding_px * (len(row) - 1)
        s = (canvas_w_px - pad_total) / rw if rw else 1.0
        scales.append(s)
    min_s  = min(scales)
    max_s  = max(scales)
    mean_s = sum(scales) / len(scales)
    counts = [len(r) for r in rows]
    print(f"    {len(rows)} rows  |  "
          f"imgs/row: {min(counts)}–{max(counts)}  |  "
          f"scale: {min_s:.3f}–{max_s:.3f}  mean {mean_s:.3f}  |  "
          f"rotation ±{cfg.rotation_max_deg}°")

    print("🎨  Compositing …")
    collage = build_canvas(rows, canvas_w_px, cfg, layout_rng)

    print("📄  Saving PDF …")
    save_pdf(collage, Path(args.output), cfg)


if __name__ == "__main__":
    main()
