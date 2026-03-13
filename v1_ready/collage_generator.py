#!/usr/bin/env python3
"""
collage_generator.py
Generates a playful photo collage PDF from a folder of images.

All tuneable parameters live in collage.ini.  Image ordering is controlled
via --order and an optional order.txt manifest in the source folder.

Usage:
    python collage_generator.py <folder> [output.pdf] [options]

Ordering modes (--order):
    filename   Sort alphabetically by filename (default when no order.txt exists)
    shuffle    Randomise order (use --seed for reproducibility)
    manifest   Read sequence from order.txt in the source folder

Manifest export (--save-manifest):
    After resolving the order (in any mode), write the final sequence to
    order.txt so it can be reviewed, tweaked, or locked in for future runs.

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


# ── Config dataclass ──────────────────────────────────────────────────────────

@dataclass
class Config:
    # [canvas]
    width_cm: float
    dpi: int
    background_color: tuple
    transparent_background: bool

    # [layout]
    order: str               # filename | shuffle | manifest
    shuffle_seed: int        # -1 = true random
    row_target_height: float
    min_images_per_row: int
    max_images_per_row: int
    padding_px: int
    row_gap_px: int

    # [decoration]
    border_px: int
    corner_radius_px: int
    rotation_max_deg: float
    overlap_tolerance_px: int


DEFAULT_INI = Path(__file__).with_name("collage.ini")


def load_config(ini_path: Path) -> Config:
    """Parse collage.ini → validated Config."""
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
    deco   = p["decoration"]

    transparent = canvas.getboolean("transparent_background", fallback=False)

    order = layout.get("order", "filename").strip().lower()
    if order not in ORDER_MODES:
        sys.exit(f"Invalid order={order!r} in INI. Must be one of: {ORDER_MODES}")

    return Config(
        width_cm               = canvas.getfloat("width_cm"),
        dpi                    = canvas.getint("dpi"),
        background_color       = None if transparent else _color(canvas["background_color"]),
        transparent_background = transparent,
        order                  = order,
        shuffle_seed           = layout.getint("shuffle_seed", fallback=-1),
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
    return int(cm_val / 2.54 * dpi)


# ── Ordering / manifest ───────────────────────────────────────────────────────

def discover_images(folder: Path) -> list:
    """Return all supported image paths in the folder (unsorted)."""
    return [p for p in folder.iterdir() if p.suffix.lower() in SUPPORTED_EXTS]


def read_manifest(folder: Path) -> list:
    """
    Parse order.txt from `folder`.  Lines starting with # and blank lines are
    ignored.  Each remaining line is a filename (not a full path) relative to
    `folder`.  Missing files produce a warning and are skipped.
    """
    manifest = folder / MANIFEST_FILENAME
    if not manifest.exists():
        sys.exit(
            f"order.txt not found in {folder}.\n"
            "Run with --order shuffle or --order filename and add --save-manifest "
            "to generate one, then edit it before using --order manifest."
        )

    lines  = manifest.read_text(encoding="utf-8").splitlines()
    paths  = []
    warned = False

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        p = folder / line
        if not p.exists():
            print(f"  ⚠  Manifest: file not found, skipping — {line}")
            warned = True
            continue
        paths.append(p)

    if not paths:
        sys.exit("order.txt exists but contains no valid image paths.")

    return paths


def resolve_order(folder: Path, cfg: Config, cli_order: Optional[str],
                  cli_seed: Optional[int]):
    """
    Determine the final ordered list of image paths.

    Priority (highest → lowest):
      1. --order CLI flag
      2. order = ... in collage.ini
      3. auto-detect: use manifest if order.txt exists, else filename

    Returns (paths, effective_mode, effective_seed).
    """
    # Determine effective mode
    if cli_order:
        mode = cli_order
    else:
        mode = cfg.order  # from INI

    # Auto-detect: if mode still unresolved default, pick based on manifest presence
    if mode == "filename" and (folder / MANIFEST_FILENAME).exists():
        print(f"  ℹ  order.txt found — switching to manifest mode automatically.\n"
              f"     Pass --order filename to override.")
        mode = "manifest"

    # Determine effective seed for shuffle
    seed: Optional[int] = None
    if mode == "shuffle":
        if cli_seed is not None:
            seed = cli_seed
        elif cfg.shuffle_seed != -1:
            seed = cfg.shuffle_seed
        # else seed=None → true random

    # Resolve paths
    if mode == "manifest":
        paths = read_manifest(folder)
        print(f"📋  Order: manifest ({len(paths)} images from order.txt)")

    elif mode == "shuffle":
        paths = sorted(discover_images(folder))  # stable base before shuffle
        rng   = random.Random(seed)
        rng.shuffle(paths)
        seed_label = str(seed) if seed is not None else "random"
        print(f"🔀  Order: shuffle  (seed={seed_label}, {len(paths)} images)")

    else:  # filename
        paths = sorted(discover_images(folder))
        print(f"🔤  Order: filename ({len(paths)} images)")

    if not paths:
        sys.exit(f"No images found in {folder}")

    return paths, mode, seed


def write_manifest(folder: Path, paths: list, mode: str,
                   seed: Optional[int]) -> None:
    """
    Write the resolved image order to order.txt in `folder`.
    Existing file is overwritten.  A header comment records provenance.
    """
    manifest = folder / MANIFEST_FILENAME
    ts        = datetime.now().strftime("%Y-%m-%d %H:%M")
    seed_note = f", seed={seed}" if seed is not None else ""
    header    = (
        f"# order.txt — generated by collage_generator.py\n"
        f"# Source mode : {mode}{seed_note}\n"
        f"# Generated   : {ts}\n"
        f"# Edit freely : lines starting with # are comments, blank lines are ignored.\n"
        f"# To use this file: set  order = manifest  in collage.ini\n"
        f"#   or run with:    --order manifest\n"
        f"#\n"
    )
    lines = header + "\n".join(p.name for p in paths) + "\n"
    manifest.write_text(lines, encoding="utf-8")
    print(f"💾  Manifest saved → {manifest}  ({len(paths)} entries)")


# ── Image decoration ──────────────────────────────────────────────────────────

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


# ── Debug labelling ───────────────────────────────────────────────────────────

def stamp_filename(img: Image.Image, name: str) -> Image.Image:
    """
    Burn the filename onto the image as a semi-transparent label bar
    anchored to the bottom edge.  Works on both RGB and RGBA inputs.
    Font size scales with image width so the label is always legible
    regardless of how the image is scaled during row layout.
    """
    from PIL import ImageFont

    img  = img.convert("RGBA")
    w, h = img.size

    # ── Font: try to load a system monospace, fall back to Pillow default ──
    font_size = max(10, w // 14)
    font: ImageFont.ImageFont
    candidates = [
        "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf",
        "Courier New Bold.ttf", "CourierNewBold.ttf", "Courier New.ttf",
        "LiberationMono-Regular.ttf", "UbuntuMono-R.ttf",
    ]
    font = None
    for name_candidate in candidates:
        try:
            font = ImageFont.truetype(name_candidate, font_size)
            break
        except (IOError, OSError):
            continue
    if font is None:
        font = ImageFont.load_default()

    # ── Measure text to size the bar ──────────────────────────────────────
    dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox  = dummy.textbbox((0, 0), name, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad_x  = max(4, w // 40)
    pad_y  = max(3, text_h // 3)
    bar_h  = text_h + pad_y * 2

    # ── Draw semi-transparent bar + white text onto a copy ────────────────
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    # Dark bar across full width at the bottom
    draw.rectangle([(0, h - bar_h), (w, h)], fill=(0, 0, 0, 180))

    # Centred text
    tx = (w - text_w) // 2
    ty = h - bar_h + pad_y
    draw.text((tx, ty), name, font=font, fill=(255, 255, 255, 230))

    result = Image.alpha_composite(img, overlay)
    return result



def load_images(paths: list, cfg: Config, canvas_width_px: int,
                label_filenames: bool = False) -> list:
    """Load images in the given order, resize, and apply decoration."""
    target_h  = max(1, int(canvas_width_px * cfg.row_target_height))
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


# ── Layout ────────────────────────────────────────────────────────────────────

def arrange_rows(images: list, canvas_width_px: int,
                 cfg: Config) -> list:
    """
    Chunk images into rows of [min, max] count, then uniform-scale each row
    so its total *rotated* width fills canvas_width_px exactly — preventing
    overlap after rotation in build_canvas().
    """
    pad      = cfg.padding_px
    max_rad  = math.radians(cfg.rotation_max_deg)
    target_n = (cfg.min_images_per_row + cfg.max_images_per_row) // 2
    rows: list = []
    i, n = 0, len(images)

    def _rot_w(img: Image.Image) -> float:
        """Worst-case rotated bounding-box width at max_rad."""
        return img.width * math.cos(max_rad) + img.height * math.sin(max_rad)

    while i < n:
        remaining  = n - i
        chunk_size = min(target_n, cfg.max_images_per_row)
        if remaining - chunk_size < cfg.min_images_per_row:
            chunk_size = remaining

        chunk  = images[i : i + chunk_size]
        i     += chunk_size

        total_pad     = pad * (len(chunk) - 1)
        natural_rot_w = sum(_rot_w(img) for img in chunk)
        scale         = (canvas_width_px - total_pad) / natural_rot_w if natural_rot_w else 1.0

        scaled = []
        for img in chunk:
            new_w = max(1, int(img.width  * scale))
            new_h = max(1, int(img.height * scale))
            scaled.append(img.resize((new_w, new_h), Image.LANCZOS))

        rows.append(scaled)

    return rows


# ── Compositing ───────────────────────────────────────────────────────────────

def build_canvas(rows: list, canvas_width_px: int,
                 cfg: Config, rng: random.Random) -> Image.Image:
    """
    Two-pass composite:
      Pass 1 — pre-rotate every image, accumulate total canvas height.
      Pass 2 — paste using actual rotated widths for spacing (no overlap).
    """
    mode = "RGBA" if cfg.transparent_background else "RGB"
    bg   = (0, 0, 0, 0) if cfg.transparent_background else cfg.background_color
    gap  = cfg.row_gap_px

    # Pass 1
    pre: list = []
    total_h = gap

    for row in rows:
        angles  = [rng.uniform(-cfg.rotation_max_deg, cfg.rotation_max_deg)
                   for _ in row]
        rotated = [img.rotate(a, expand=True, resample=Image.BICUBIC)
                   for img, a in zip(row, angles)]
        row_h   = max(r.height for r in rotated)
        pre.append((rotated, angles))
        total_h += row_h + gap

    # Pass 2
    canvas = Image.new(mode, (canvas_width_px, total_h), bg)
    y = gap

    for (rotated, _), _row in zip(pre, rows):
        row_h       = max(r.height for r in rotated)
        total_rot_w = sum(r.width for r in rotated)
        n_imgs      = len(rotated)
        total_gaps  = canvas_width_px - total_rot_w
        inter_gap   = max(cfg.overlap_tolerance_px, total_gaps // (n_imgs + 1))

        x = inter_gap
        for img_r in rotated:
            cy = y + (row_h - img_r.height) // 2
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
          f"{collage.width}×{collage.height}px)")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Playful photo collage → PDF",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("folder",
                        help="Folder containing source images")
    parser.add_argument("output", nargs="?", default="collage.pdf",
                        help="Output PDF path")
    parser.add_argument("--config", type=Path, default=DEFAULT_INI,
                        help="Path to collage.ini")
    parser.add_argument("--order",  choices=ORDER_MODES, default=None,
                        help="Image ordering mode (overrides INI setting). "
                             "filename=alphabetical, shuffle=random, "
                             "manifest=read order.txt")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed for shuffle mode (overrides INI shuffle_seed)")
    parser.add_argument("--save-manifest", action="store_true",
                        help="Write the resolved image order to order.txt "
                             "in the source folder (works with any --order mode)")
    parser.add_argument("--label-files", action="store_true",
                        help="[DEBUG] Burn each image's filename onto the photo "
                             "as a label bar — useful for identifying pictures "
                             "to reorder in the manifest. Not for final output.")
    args = parser.parse_args()

    cfg    = load_config(args.config)
    folder = Path(args.folder)

    if not folder.is_dir():
        sys.exit(f"Not a directory: {folder}")

    # ── Resolve ordering ──────────────────────────────────────────────────────
    paths, effective_mode, effective_seed = resolve_order(
        folder, cfg, args.order, args.seed
    )

    # ── Optionally save manifest ──────────────────────────────────────────────
    if args.save_manifest:
        write_manifest(folder, paths, effective_mode, effective_seed)

    # ── Build collage ─────────────────────────────────────────────────────────
    canvas_w_px = px(cfg.width_cm, cfg.dpi)

    # Separate RNG for layout (rotation/spacing) so --seed only affects ordering
    layout_rng = random.Random(effective_seed)

    print(f"🖼   Loading {len(paths)} images …")
    images = load_images(paths, cfg, canvas_w_px,
                         label_filenames=args.label_files)
    if args.label_files:
        print("    ⚠  DEBUG: filenames stamped on images — not for final output")

    print("🗂   Arranging rows …")
    rows = arrange_rows(images, canvas_w_px, cfg)
    print(f"    {len(rows)} rows  |  "
          f"{cfg.min_images_per_row}–{cfg.max_images_per_row} imgs/row  |  "
          f"rotation ±{cfg.rotation_max_deg}°  |  corners r={cfg.corner_radius_px}px")

    print("🎨  Compositing …")
    collage = build_canvas(rows, canvas_w_px, cfg, layout_rng)

    print("📄  Saving PDF …")
    save_pdf(collage, Path(args.output), cfg)


if __name__ == "__main__":
    main()
