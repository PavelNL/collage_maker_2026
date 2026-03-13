#!/usr/bin/env python3
"""
collage_generator.py
Generates a playful photo collage PDF from a folder of images.

All tuneable parameters are read from collage.ini (same directory as this
script, or pass --config to override).

Usage:
    python collage_generator.py <input_folder> [output.pdf] [--config collage.ini] [--seed 42]

Requirements:
    pip install Pillow reportlab
"""

import argparse
import configparser
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas


# ── Config dataclass ──────────────────────────────────────────────────────────

@dataclass
class Config:
    # [canvas]
    width_cm: float
    dpi: int
    background_color: tuple   # None = transparent
    transparent_background: bool

    # [layout]
    row_target_height: float
    min_images_per_row: int
    max_images_per_row: int
    padding_px: int
    row_gap_px: int

    # [decoration]
    border_px: int
    corner_radius_px: int
    rotation_max_deg: float
    overlap_tolerance_px: int  # max permitted pixel bleed between adjacent images


DEFAULT_INI = Path(__file__).with_name("collage.ini")


def load_config(ini_path: Path) -> Config:
    """Parse collage.ini and return a validated Config object."""
    if not ini_path.exists():
        sys.exit(f"Config file not found: {ini_path}\n"
                 f"Create it or pass --config <path>.")

    p = configparser.ConfigParser()
    p.read(ini_path)

    def _color(raw: str):
        parts = [int(x.strip()) for x in raw.split(",")]
        if len(parts) != 3:
            raise ValueError(f"Expected R,G,B — got: {raw!r}")
        return tuple(parts)

    canvas  = p["canvas"]
    layout  = p["layout"]
    deco    = p["decoration"]

    transparent = canvas.getboolean("transparent_background", fallback=False)

    return Config(
        width_cm               = canvas.getfloat("width_cm"),
        dpi                    = canvas.getint("dpi"),
        background_color       = None if transparent else _color(canvas["background_color"]),
        transparent_background = transparent,
        row_target_height      = layout.getfloat("row_target_height"),
        min_images_per_row     = layout.getint("min_images_per_row"),
        max_images_per_row     = layout.getint("max_images_per_row"),
        padding_px             = layout.getint("padding_px"),
        row_gap_px             = layout.getint("row_gap_px"),
        border_px              = deco.getint("border_px"),
        corner_radius_px       = deco.getint("corner_radius_px"),
        rotation_max_deg       = deco.getfloat("rotation_max_deg"),
        overlap_tolerance_px   = deco.getint("overlap_tolerance_px", fallback=3),
    )


# ── Unit helpers ──────────────────────────────────────────────────────────────

def px(cm_val: float, dpi: int) -> int:
    """Convert cm → pixels at the given DPI."""
    return int(cm_val / 2.54 * dpi)


def rotated_bbox(w: int, h: int, deg: float) -> tuple[int, int]:
    """
    Return the (width, height) of the bounding box after rotating a rectangle
    of size (w, h) by `deg` degrees — identical to Pillow's expand=True logic.
    """
    import math
    rad  = math.radians(abs(deg))
    new_w = int(w * math.cos(rad) + h * math.sin(rad)) + 1
    new_h = int(w * math.sin(rad) + h * math.cos(rad)) + 1
    return new_w, new_h


# ── Image decoration ──────────────────────────────────────────────────────────

def add_border(img: Image.Image, border: int) -> Image.Image:
    if border <= 0:
        return img
    return ImageOps.expand(img, border=border, fill=(255, 255, 255))


def round_corners(img: Image.Image, radius: int) -> Image.Image:
    """Apply rounded corners via RGBA alpha mask."""
    if radius <= 0:
        return img
    img = img.convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)
    img.putalpha(mask)
    return img


# ── Loading ───────────────────────────────────────────────────────────────────

def load_images(folder: Path, cfg: Config, canvas_width_px: int) -> list:
    """Load all images, resize to row target height, apply decoration."""
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
    paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in exts)
    if not paths:
        sys.exit(f"No images found in {folder}")

    target_h = max(1, int(canvas_width_px * cfg.row_target_height))
    processed = []

    for p in paths:
        img = Image.open(p).convert("RGB")
        ratio = target_h / img.height
        new_w = max(1, int(img.width * ratio))
        img = img.resize((new_w, target_h), Image.LANCZOS)
        img = add_border(img, cfg.border_px)
        img = round_corners(img, cfg.corner_radius_px)
        processed.append(img)

    return processed


# ── Layout ────────────────────────────────────────────────────────────────────

def arrange_rows(images: list, canvas_width_px: int, cfg: Config) -> list:
    """
    Chunk images into rows of size in [min, max], then scale each row
    uniformly so its total *rotated* width fills canvas_width_px exactly,
    guaranteeing no overlap after rotation is applied in build_canvas().

    Strategy:
      - Compute worst-case rotated width for each image at rotation_max_deg.
      - Scale the row so the sum of rotated widths + padding == canvas_width_px.
      - Store the pre-rotation (smaller) images; build_canvas() rotates them
        and their bounding boxes will fit cleanly within the allocated slots.
    """
    import math
    pad      = cfg.padding_px
    max_rad  = math.radians(cfg.rotation_max_deg)
    target_n = (cfg.min_images_per_row + cfg.max_images_per_row) // 2
    rows     = []
    i, n     = 0, len(images)

    while i < n:
        remaining  = n - i
        chunk_size = min(target_n, cfg.max_images_per_row)
        # Absorb a too-small tail into this row
        if remaining - chunk_size < cfg.min_images_per_row:
            chunk_size = remaining

        chunk = images[i : i + chunk_size]
        i    += chunk_size

        # Worst-case rotated width for each image at max rotation angle.
        # rotated_bbox_w = w·|cos θ| + h·|sin θ|  (Pillow expand=True formula)
        def _rot_w(img: Image.Image) -> float:
            return img.width * math.cos(max_rad) + img.height * math.sin(max_rad)

        total_pad     = pad * (len(chunk) - 1)
        natural_rot_w = sum(_rot_w(img) for img in chunk)
        # Scale so rotated widths exactly fill the canvas minus padding
        scale = (canvas_width_px - total_pad) / natural_rot_w if natural_rot_w else 1.0

        scaled = []
        for img in chunk:
            new_w = max(1, int(img.width  * scale))
            new_h = max(1, int(img.height * scale))
            scaled.append(img.resize((new_w, new_h), Image.LANCZOS))

        rows.append(scaled)

    return rows


# ── Compositing ───────────────────────────────────────────────────────────────

def build_canvas(rows: list, canvas_width_px: int, cfg: Config, rng: random.Random) -> Image.Image:
    """Composite all rows onto a single tall canvas with random per-image rotation.

    Spacing is computed from *actual* rotated bounding boxes so images never
    overlap by more than cfg.overlap_tolerance_px (default: 2–4 px rounding).
    """

    mode = "RGBA" if cfg.transparent_background else "RGB"
    bg   = (0, 0, 0, 0) if cfg.transparent_background else cfg.background_color
    pad  = cfg.padding_px
    gap  = cfg.row_gap_px

    # Pass 1 — pre-rotate and measure total height
    pre     = []
    total_h = gap  # top margin

    for row in rows:
        angles  = [rng.uniform(-cfg.rotation_max_deg, cfg.rotation_max_deg) for _ in row]
        rotated = [img.rotate(a, expand=True, resample=Image.BICUBIC)
                   for img, a in zip(row, angles)]
        row_h   = max(r.height for r in rotated)
        pre.append((rotated, angles))
        total_h += row_h + gap

    # Pass 2 — composite using rotated widths for gap calculation
    canvas = Image.new(mode, (canvas_width_px, total_h), bg)

    y = gap
    for (rotated, _), row in zip(pre, rows):
        row_h = max(r.height for r in rotated)

        # Use actual post-rotation widths — this is the key fix.
        # inter_gap is now computed from rotated bounding boxes, so each image
        # lands in a slot wide enough to contain its rotated corners.
        total_rot_w = sum(r.width for r in rotated)
        n_imgs      = len(rotated)
        total_gaps  = canvas_width_px - total_rot_w
        inter_gap   = max(cfg.overlap_tolerance_px, total_gaps // (n_imgs + 1))

        x = inter_gap
        for img_r in rotated:
            cy = y + (row_h - img_r.height) // 2
            if img_r.mode == "RGBA":
                canvas.paste(img_r, (x, cy), img_r.split()[3])
            else:
                canvas.paste(img_r, (x, cy))
            x += img_r.width + inter_gap

        y += row_h + gap

    return canvas


# ── Export ────────────────────────────────────────────────────────────────────

def save_pdf(collage: Image.Image, output_path: Path, cfg: Config) -> None:
    """Save the composited image as a single-page PDF at physical dimensions."""
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
        description="Playful photo collage → PDF",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("folder",  help="Folder containing source images")
    parser.add_argument("output",  nargs="?", default="collage.pdf",
                        help="Output PDF path")
    parser.add_argument("--config", type=Path, default=DEFAULT_INI,
                        help="Path to collage.ini config file")
    parser.add_argument("--seed",  type=int, default=None,
                        help="RNG seed for reproducible layout")
    args = parser.parse_args()

    cfg    = load_config(args.config)
    rng    = random.Random(args.seed)
    folder = Path(args.folder)

    if not folder.is_dir():
        sys.exit(f"Not a directory: {folder}")

    canvas_w_px = px(cfg.width_cm, cfg.dpi)

    print(f"📂  Loading images from {folder} …")
    images = load_images(folder, cfg, canvas_w_px)
    rng.shuffle(images)
    print(f"    {len(images)} images  |  "
          f"rows: {cfg.min_images_per_row}–{cfg.max_images_per_row} imgs/row  |  "
          f"rotation ±{cfg.rotation_max_deg}°  |  "
          f"corners r={cfg.corner_radius_px}px")

    print("🗂   Arranging rows …")
    rows = arrange_rows(images, canvas_w_px, cfg)
    print(f"    {len(rows)} rows")

    print("🎨  Compositing …")
    collage = build_canvas(rows, canvas_w_px, cfg, rng)

    print("📄  Saving PDF …")
    save_pdf(collage, Path(args.output), cfg)


if __name__ == "__main__":
    main()
