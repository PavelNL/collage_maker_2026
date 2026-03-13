#!/usr/bin/env python3
"""
collage_generator.py
Generates a playful photo collage PDF from a folder of images.

Usage:
    python collage_generator.py <input_folder> [output.pdf] [--seed 42] [--dpi 150]

Requirements:
    pip install Pillow reportlab
"""

import argparse
import math
import os
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas

# ── Layout constants ──────────────────────────────────────────────────────────
CANVAS_WIDTH_CM   = 60.0          # fixed paper width
DPI_DEFAULT       = 150           # render resolution (increase for print quality)
PADDING_PX        = 20            # gap between images (pixels at chosen DPI)
BORDER_PX         = 8             # white border around each photo
CORNER_RADIUS_PX  = 24            # rounded corner radius
ROTATION_MAX_DEG  = 12            # max ±rotation per image
ROW_TARGET_HEIGHT = 0.22          # target row height as fraction of canvas width
MIN_IMAGES_PER_ROW = 1
MAX_IMAGES_PER_ROW = 5
BACKGROUND_COLOR  = (245, 242, 235)  # warm off-white


def px(cm_val: float, dpi: int) -> int:
    """Convert cm → pixels."""
    return int(cm_val / 2.54 * dpi)


def add_border(img: Image.Image, border: int) -> Image.Image:
    return ImageOps.expand(img, border=border, fill=(255, 255, 255))


def round_corners(img: Image.Image, radius: int) -> Image.Image:
    """Apply rounded corners via alpha mask (works on RGBA)."""
    img = img.convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)
    img.putalpha(mask)
    return img


def rotate_image(img: Image.Image, angle: float) -> Image.Image:
    """Rotate with expand so corners aren't clipped; background transparent."""
    return img.rotate(angle, expand=True, resample=Image.BICUBIC)


def load_images(folder: Path, dpi: int, canvas_width_px: int) -> list[Image.Image]:
    """Load, resize, decorate all images from folder."""
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
    paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in exts)
    if not paths:
        sys.exit(f"No images found in {folder}")

    target_h = int(canvas_width_px * ROW_TARGET_HEIGHT)
    processed = []
    for p in paths:
        img = Image.open(p).convert("RGB")
        # Scale so height = target_h (width scales proportionally)
        ratio = target_h / img.height
        new_w = max(1, int(img.width * ratio))
        img = img.resize((new_w, target_h), Image.LANCZOS)
        img = add_border(img, BORDER_PX)
        img = round_corners(img, CORNER_RADIUS_PX)
        processed.append(img)

    return processed


def arrange_rows(images: list[Image.Image], canvas_width_px: int, padding: int
                 ) -> list[list[Image.Image]]:
    """
    Greedy row packing: fill rows left-to-right until adding the next image
    would exceed canvas width (accounting for padding). Shuffle within rows
    for variety while keeping all images.
    """
    rows: list[list[Image.Image]] = []
    current_row: list[Image.Image] = []
    current_w = 0

    for img in images:
        w = img.width
        extra_pad = padding if current_row else 0
        if current_w + extra_pad + w > canvas_width_px and current_row:
            rows.append(current_row)
            current_row = [img]
            current_w = w
        else:
            current_row.append(img)
            current_w += extra_pad + w

    if current_row:
        rows.append(current_row)

    return rows


def build_canvas(rows: list[list[Image.Image]],
                 canvas_width_px: int,
                 padding: int,
                 rng: random.Random) -> Image.Image:
    """Composite all rows onto a single tall canvas with random rotations."""

    # First pass: compute total canvas height
    total_height = padding  # top margin
    row_heights = []
    for row in rows:
        # After rotation each image bounding box may be taller
        angles = [rng.uniform(-ROTATION_MAX_DEG, ROTATION_MAX_DEG) for _ in row]
        rotated = [img.rotate(a, expand=True, resample=Image.BICUBIC)
                   for img, a in zip(row, angles)]
        row_h = max(r.height for r in rotated) + padding
        row_heights.append((row_h, angles, rotated))
        total_height += row_h

    total_height += padding  # bottom margin

    canvas = Image.new("RGB", (canvas_width_px, total_height), BACKGROUND_COLOR)

    y = padding
    for (row_h, angles, rotated), orig_row in zip(row_heights, rows):
        # Scale rotated images so they fit row height nicely
        usable_w = canvas_width_px - 2 * padding
        total_img_w = sum(r.width for r in rotated) + padding * (len(rotated) - 1)

        # Distribute any leftover space evenly
        leftover = usable_w - total_img_w
        extra_pad = leftover // max(len(rotated), 1)

        x = padding + extra_pad // 2
        for img_r in rotated:
            # Vertical centering within the row
            cy = y + (row_h - img_r.height) // 2
            # Paste with alpha mask (rounded corners)
            if img_r.mode == "RGBA":
                canvas.paste(img_r, (x, cy), img_r.split()[3])
            else:
                canvas.paste(img_r, (x, cy))
            x += img_r.width + padding + extra_pad

        y += row_h

    return canvas


def save_pdf(collage: Image.Image, output_path: Path, dpi: int,
             canvas_width_cm: float) -> None:
    """Save PIL image as PDF at the given physical size."""
    width_pt  = canvas_width_cm * cm
    height_pt = collage.height / collage.width * width_pt

    c = rl_canvas.Canvas(str(output_path), pagesize=(width_pt, height_pt))
    # Save image to a temp file for reportlab
    tmp = output_path.with_suffix(".tmp.png")
    collage.save(tmp, "PNG")
    c.drawImage(str(tmp), 0, 0, width=width_pt, height=height_pt)
    c.save()
    tmp.unlink(missing_ok=True)
    print(f"✓ Saved → {output_path}  ({canvas_width_cm}cm × "
          f"{height_pt / cm:.1f}cm  |  {collage.width}×{collage.height}px)")


def main():
    parser = argparse.ArgumentParser(description="Playful photo collage → PDF")
    parser.add_argument("folder", help="Folder containing source images")
    parser.add_argument("output", nargs="?", default="collage.pdf",
                        help="Output PDF path (default: collage.pdf)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducible layout")
    parser.add_argument("--dpi", type=int, default=DPI_DEFAULT,
                        help=f"Render DPI (default: {DPI_DEFAULT})")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    folder = Path(args.folder)
    if not folder.is_dir():
        sys.exit(f"Not a directory: {folder}")

    canvas_w_px = px(CANVAS_WIDTH_CM, args.dpi)
    padding_px  = PADDING_PX

    print(f"📂 Loading images from {folder} …")
    images = load_images(folder, args.dpi, canvas_w_px)
    rng.shuffle(images)
    print(f"   {len(images)} images loaded")

    print("🗂  Arranging rows …")
    rows = arrange_rows(images, canvas_w_px, padding_px)
    print(f"   {len(rows)} rows")

    print("🎨 Compositing collage …")
    collage = build_canvas(rows, canvas_w_px, padding_px, rng)

    output_path = Path(args.output)
    print("📄 Saving PDF …")
    save_pdf(collage, output_path, args.dpi, CANVAS_WIDTH_CM)


if __name__ == "__main__":
    main()
