# Collage Generator — Project Context

> **Claude Code context file.** Load this at session start to resume work on this project.
> Keep this file updated after every significant change.

---

## Project Overview

A Python CLI tool that takes a folder of photos and produces a single **PDF collage** with a fixed physical width of **60 cm** and auto-calculated height. The aesthetic is deliberately "playful": images are slightly rotated, have optional white borders, and rounded corners. All tuneable parameters live in `collage.ini` — the script contains no magic numbers.

---

## File Structure

```
project/
├── collage_generator.py   # Main script — single-file, no package structure
├── collage.ini            # All tuneable parameters (INI format)
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

Python version: **3.10+**

---

## Usage

```bash
# Basic (reads collage.ini from script directory automatically)
python collage_generator.py ./photos output.pdf

# Custom config file
python collage_generator.py ./photos output.pdf --config /path/to/custom.ini

# Reproducible layout
python collage_generator.py ./photos output.pdf --seed 42
```

### CLI Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `folder` | positional | required | Path to source image folder |
| `output` | positional | `collage.pdf` | Output PDF path |
| `--config` | Path | `./collage.ini` | INI config file path |
| `--seed` | int | `None` | RNG seed for reproducible layout |

---

## collage.ini Reference

All tuneable parameters. The script reads this file at startup.

```ini
[canvas]
width_cm = 60.0                 # Physical paper width in cm (height is auto)
dpi = 150                       # 150=preview, 300=print quality
background_color = 255,255,255  # R,G,B — ignored if transparent_background=true
transparent_background = false  # true → RGBA canvas, mask="auto" in PDF

[layout]
row_target_height = 0.13        # Image height as fraction of canvas width
min_images_per_row = 7          # Minimum images forced into each row
max_images_per_row = 8          # Maximum images per row
padding_px = 4                  # Horizontal gap between images (px at DPI)
row_gap_px = 2                  # Vertical gap between rows (px at DPI)

[decoration]
border_px = 0                   # White border per image; 0 = disabled
corner_radius_px = 10           # Rounded corner radius; 0 = sharp corners
rotation_max_deg = 8            # Max ±random rotation per image; 0 = no rotation
```

---

## Architecture

### Pipeline

```
load_config(ini_path)
    └── configparser → Config dataclass (validated)

load_images(folder, cfg, canvas_width_px)
    └── Discover images (jpg, jpeg, png, webp, bmp, tiff)
    └── Resize each → row_target_height (proportional)
    └── add_border()     — conditional on border_px > 0
    └── round_corners()  — conditional on corner_radius_px > 0

arrange_rows(images, canvas_width_px, cfg)
    └── Chunk into groups of ~target_n (between min and max per row)
    └── Uniform-scale each chunk so total width == canvas_width_px

build_canvas(rows, canvas_width_px, cfg, rng)
    └── Pass 1: pre-rotate all images, accumulate total height
    └── Pass 2: composite with equal inter-image spacing
    └── RGBA paste respects alpha mask from round_corners

save_pdf(collage, output_path, cfg)
    └── reportlab Canvas at physical dimensions (cm → points)
    └── tmp PNG → drawImage → remove tmp
    └── mask="auto" when transparent_background=true
```

### Key Types

| Name | Type | Purpose |
|---|---|---|
| `Config` | `@dataclass` | Single source of truth for all runtime parameters |
| `DEFAULT_INI` | `Path` | Resolved relative to `__file__` so script is location-independent |

### Key Functions

| Function | Purpose |
|---|---|
| `load_config(ini_path)` | Parse and validate collage.ini → `Config` |
| `px(cm_val, dpi)` | Unit conversion cm → pixels |
| `add_border(img, border)` | No-op when `border <= 0` |
| `round_corners(img, radius)` | No-op when `radius <= 0`; uses RGBA alpha mask |
| `load_images(folder, cfg, canvas_width_px)` | Full load + decorate pipeline |
| `arrange_rows(images, canvas_width_px, cfg)` | Chunk + uniform-scale to enforce min/max per row |
| `build_canvas(rows, canvas_width_px, cfg, rng)` | Two-pass composite with random rotation |
| `save_pdf(collage, output_path, cfg)` | reportlab PDF export |

---

## Known Limitations & Potential Improvements

| # | Area | Issue | Suggested Fix |
|---|---|---|---|
| 1 | Layout | Last row may have fewer images than min_images_per_row (tail absorption) | Accept as intentional; or scale last row to match |
| 2 | Performance | All images decoded at full DPI before layout | Decode thumbnail first, re-decode at full res post-layout |
| 3 | PDF | Uses a temp `.tmp.png` on disk | Use `io.BytesIO` to keep in-memory |
| 4 | Variety | All images in a row scaled to the same height | Add ±N% random height jitter per image for more organic feel |
| 5 | CLI | `--dpi` removed (now in INI) | Consider re-exposing as CLI override that trumps INI |
| 6 | Config | No validation ranges (e.g. negative radius silently accepted as no-op) | Add range checks in `load_config()` |

---

## Design Decisions

- **INI over env vars / CLI flags** — parameters are project-level config, not per-run overrides. Keeps the command line clean.
- **`Config` dataclass** — single validated object passed through the entire pipeline; no global state.
- **`DEFAULT_INI = Path(__file__).with_name("collage.ini")`** — script resolves its config relative to itself, so it works regardless of cwd.
- **Uniform row scaling** — enforcing min/max images per row by scaling (rather than reflowing) guarantees tight, justified rows without complex bin-packing.
- **`border_px = 0` / `corner_radius_px = 0` as disabling sentinel** — avoids boolean flags; functions are no-ops at zero.
- **`transparent_background`** — preserved through the entire pipeline (RGBA canvas, `mask="auto"` in reportlab) for clean compositing in layout tools.

---

## Changelog

| Date | Change |
|---|---|
| 2026-03-13 | Initial version — 60cm PDF, greedy row layout, rotation, rounded corners, white border |
| 2026-03-13 | Extracted all tuneable params to `collage.ini`; added `Config` dataclass; enforced min/max per row via uniform scaling; added `row_gap_px`, `transparent_background`; defaults: pure white bg, no border, 7–8 imgs/row, 10px corners, ±8° rotation |

---

*Update this file whenever the script's interface, constants, architecture, or known issues change.*
