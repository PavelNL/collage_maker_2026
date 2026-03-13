# Collage Generator — Project Context

> **Claude Code context file.** Load this at session start to resume work on this project.
> Keep this file updated after every significant change.

---

## Project Overview

A Python CLI tool that takes a folder of photos and produces a single **PDF collage** with a fixed physical width of **60 cm** and auto-calculated height. The aesthetic is deliberately "playful": images are slightly rotated, have white borders, and rounded corners. Layout is greedy row-packing with even horizontal distribution.

---

## File Structure

```
project/
├── collage_generator.py   # Main script — single-file, no package structure
└── CLAUDE.md              # ← this file
```

---

## Dependencies

```
Pillow       # Image loading, transformation, compositing
reportlab    # PDF rendering at physical dimensions
```

Install:
```bash
pip install Pillow reportlab
```

Python version: **3.10+** (uses `list[...]` type hints without `from __future__`)

---

## Usage

```bash
# Basic
python collage_generator.py ./photos output.pdf

# With options
python collage_generator.py ./photos output.pdf --seed 42 --dpi 300
```

### CLI Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `folder` | positional | required | Path to source image folder |
| `output` | positional | `collage.pdf` | Output PDF path |
| `--seed` | int | `None` | RNG seed for reproducible layout |
| `--dpi` | int | `150` | Render resolution (use 300 for print) |

---

## Architecture

### Pipeline (sequential, no concurrency)

```
load_images()
    └── Open all images from folder (jpg, jpeg, png, webp, bmp, tiff)
    └── Resize each to ROW_TARGET_HEIGHT (fraction of canvas width)
    └── add_border() → white Pillow border
    └── round_corners() → RGBA alpha-mask technique

arrange_rows()
    └── Greedy left-to-right bin packing
    └── New row when next image would exceed canvas width

build_canvas()
    └── Two passes: first computes total height, second composites
    └── Random rotation per image (±ROTATION_MAX_DEG)
    └── Vertical centering per row
    └── RGBA paste with alpha mask for rounded corners

save_pdf()
    └── Computes PDF page size in points from cm
    └── Saves temp PNG → drawImage via reportlab → removes temp file
```

### Key Functions

| Function | Signature | Purpose |
|---|---|---|
| `px(cm_val, dpi)` | `(float, int) → int` | Unit conversion cm → pixels |
| `add_border(img, border)` | `(Image, int) → Image` | White border via `ImageOps.expand` |
| `round_corners(img, radius)` | `(Image, int) → Image` | Alpha mask rounded rectangle |
| `rotate_image(img, angle)` | `(Image, float) → Image` | Expand-rotate (not used directly; inlined in `build_canvas`) |
| `load_images(folder, dpi, canvas_width_px)` | `(Path, int, int) → list[Image]` | Full load + decorate pipeline |
| `arrange_rows(images, canvas_width_px, padding)` | `(list[Image], int, int) → list[list[Image]]` | Row packing |
| `build_canvas(rows, canvas_width_px, padding, rng)` | `(...) → Image` | Composite final image |
| `save_pdf(collage, output_path, dpi, canvas_width_cm)` | `(Image, Path, int, float) → None` | Export to PDF |

---

## Layout Constants (top of `collage_generator.py`)

All tuneable parameters are module-level constants:

```python
CANVAS_WIDTH_CM   = 60.0          # Physical paper width — do not change lightly
DPI_DEFAULT       = 150           # 150 = screen/preview, 300 = print
PADDING_PX        = 20            # Gap between images (at DPI)
BORDER_PX         = 8             # White photo border thickness
CORNER_RADIUS_PX  = 24            # Rounded corner radius
ROTATION_MAX_DEG  = 12            # Max ±random rotation per image
ROW_TARGET_HEIGHT = 0.22          # Images scaled to this fraction of canvas width
MIN_IMAGES_PER_ROW = 1            # (reserved, not enforced in current packing)
MAX_IMAGES_PER_ROW = 5            # (reserved, not enforced in current packing)
BACKGROUND_COLOR  = (245, 242, 235)  # Warm off-white canvas fill
```

---

## Known Limitations & Potential Improvements

| # | Area | Issue | Suggested Fix |
|---|---|---|---|
| 1 | Layout | `MIN/MAX_IMAGES_PER_ROW` constants are defined but not enforced | Wire into `arrange_rows()` |
| 2 | Layout | Last row may be sparse (few images, lots of whitespace) | Scale up last-row images to fill width |
| 3 | Performance | All images decoded at full DPI before layout | Decode at thumbnail first, re-decode full-res after layout |
| 4 | PDF | Uses a temp `.tmp.png` file on disk | Use `io.BytesIO` to keep everything in-memory |
| 5 | Rotation | `rotate_image()` is defined but not called directly (logic inlined in `build_canvas`) | Either remove the standalone function or refactor to call it |
| 6 | Variety | All images scaled to the same row height | Add ±10% random height variation per image for more organic feel |
| 7 | CLI | No `--width` flag; canvas width is hardcoded as a constant | Expose `CANVAS_WIDTH_CM` as a CLI argument |

---

## Design Decisions

- **Single-file script** — no package structure, intentional for portability and simplicity.
- **Pillow over OpenCV** — sufficient for 2D compositing; no CV operations needed.
- **reportlab for PDF** — direct physical-unit control (cm → points) without Cairo/wkhtmltopdf deps.
- **Greedy packing over bin-packing solver** — fast, deterministic given a seed, good-enough aesthetics.
- **RGBA throughout decoration pipeline** — border → round_corners → rotate all preserve alpha; flattened to RGB only at canvas composite time.

---

## Changelog

| Date | Change |
|---|---|
| 2026-03-13 | Initial version — 60cm PDF, greedy row layout, rotation, rounded corners, white border |

---

*Update this file whenever the script's interface, constants, architecture, or known issues change.*
