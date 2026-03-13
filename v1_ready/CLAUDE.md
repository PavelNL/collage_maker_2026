# Collage Generator — Project Context

> **Claude Code context file.** Load this at session start to resume work on this project.
> Keep this file updated after every significant change.

---

## Project Overview

A Python CLI tool that takes a folder of photos and produces a single **PDF collage** with a fixed physical width of **60 cm** and auto-calculated height. The aesthetic is "playful": images are randomly rotated, have optional white borders and rounded corners. All tuneable parameters live in `collage.ini`. Image ordering is fully configurable via three modes.

---

## File Structure

```
project/
├── collage_generator.py   # Main script — single-file, no package structure
├── collage.ini            # All tuneable parameters (INI format)
├── order.txt              # Optional manifest (generated or hand-edited)
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
# Basic — uses order = filename from collage.ini
python collage_generator.py ./photos output.pdf

# Shuffle with a fixed seed
python collage_generator.py ./photos output.pdf --order shuffle --seed 42

# Use hand-edited manifest
python collage_generator.py ./photos output.pdf --order manifest

# Generate/overwrite order.txt from current shuffle run (then edit it)
python collage_generator.py ./photos output.pdf --order shuffle --seed 7 --save-manifest

# Generate manifest from alphabetical order
python collage_generator.py ./photos output.pdf --order filename --save-manifest

# Custom config file
python collage_generator.py ./photos output.pdf --config /path/to/custom.ini
```

### CLI Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `folder` | positional | required | Path to source image folder |
| `output` | positional | `collage.pdf` | Output PDF path |
| `--config` | Path | `./collage.ini` | INI config file path |
| `--order` | choice | *(from INI)* | `filename`, `shuffle`, or `manifest` — overrides INI |
| `--seed` | int | *(from INI)* | RNG seed for shuffle mode — overrides `shuffle_seed` in INI |
| `--save-manifest` | flag | off | Write resolved order to `order.txt` after any mode |

---

## Image Ordering

Three modes, controlled by `order =` in `[layout]` of `collage.ini` or `--order` CLI flag.

### `filename` (default)
Sorts all images in the folder alphabetically. Predictable, no extra files needed. Rename files with numeric prefixes (`001_`, `002_`, …) for full manual control.

### `shuffle`
Randomises the image list. Use `shuffle_seed` in INI (or `--seed` CLI) to lock a layout for reproducibility. `shuffle_seed = -1` → different random order every run.

### `manifest`
Reads `order.txt` from the source folder. One filename per line. Lines starting with `#` and blank lines are ignored (use for comments and visual grouping). Missing files produce a warning and are skipped.

**Auto-detect:** if `order = filename` in the INI but `order.txt` exists in the source folder, the script automatically switches to `manifest` mode with a console notice. Pass `--order filename` explicitly to override.

### `--save-manifest` flag
Works with **any** mode. After resolving the final order, writes it to `order.txt`. Typical workflow:
1. Run with `--order shuffle --seed 42 --save-manifest` to generate a starting point
2. Open `order.txt`, reorder/remove/add filenames as needed
3. Run with `--order manifest` (or just run again — auto-detect picks it up)

Generated `order.txt` includes a header comment with source mode, seed, and timestamp.

---

## collage.ini Reference

```ini
[canvas]
width_cm = 60.0                 # Physical paper width in cm (height is auto)
dpi = 150                       # 150=preview, 300=print quality
background_color = 255,255,255  # R,G,B — ignored if transparent_background=true
transparent_background = false  # true → RGBA canvas, mask="auto" in PDF

[layout]
order = filename                # filename | shuffle | manifest
shuffle_seed = -1               # fixed seed for shuffle; -1 = true random
row_target_height = 0.13        # image height as fraction of canvas width
min_images_per_row = 7
max_images_per_row = 8
padding_px = 4                  # horizontal gap between images
row_gap_px = 2                  # vertical gap between rows

[decoration]
border_px = 0                   # white border per image; 0 = disabled
corner_radius_px = 10           # rounded corner radius; 0 = sharp corners
rotation_max_deg = 8            # max ±random rotation; 0 = no rotation
overlap_tolerance_px = 3        # hard floor on gap between rotated bounding boxes
```

---

## Architecture

### Pipeline

```
load_config(ini_path)
    └── configparser → Config dataclass

resolve_order(folder, cfg, cli_order, cli_seed)
    └── Determines effective mode (CLI > INI > auto-detect)
    └── filename  → sorted(discover_images())
    └── shuffle   → sorted then rng.shuffle()
    └── manifest  → read_manifest() parses order.txt
    └── Returns (paths: list[Path], mode: str, seed: int|None)

[optional] write_manifest(folder, paths, mode, seed)
    └── Writes order.txt with provenance header comment

load_images(paths, cfg, canvas_width_px)
    └── Loads images IN RESOLVED ORDER (no internal sorting)
    └── Resize → add_border → round_corners

arrange_rows(images, canvas_width_px, cfg)
    └── Chunks into [min, max] per row
    └── Uniform-scales using worst-case rotated width to prevent overlap

build_canvas(rows, canvas_width_px, cfg, rng)
    └── Pass 1: pre-rotate, accumulate height
    └── Pass 2: composite with rotated-width-based spacing

save_pdf(collage, output_path, cfg)
    └── reportlab PDF at physical cm dimensions
```

### Key Functions

| Function | Purpose |
|---|---|
| `load_config(ini_path)` | Parse and validate collage.ini → `Config` |
| `discover_images(folder)` | Find all supported images (unsorted) |
| `read_manifest(folder)` | Parse `order.txt` → `list[Path]`, warn on missing files |
| `resolve_order(folder, cfg, cli_order, cli_seed)` | Central ordering logic, returns `(paths, mode, seed)` |
| `write_manifest(folder, paths, mode, seed)` | Serialize resolved order to `order.txt` |
| `load_images(paths, cfg, canvas_width_px)` | Load in order, resize, decorate |
| `arrange_rows(images, canvas_width_px, cfg)` | Chunk + scale rows, rotation-aware |
| `build_canvas(rows, canvas_width_px, cfg, rng)` | Two-pass composite |
| `save_pdf(collage, output_path, cfg)` | reportlab PDF export |

---

## Known Limitations & Potential Improvements

| # | Area | Issue | Suggested Fix |
|---|---|---|---|
| 1 | Layout | Last row may have fewer images than `min_images_per_row` (tail absorption) | Accept or scale last row differently |
| 2 | Performance | All images decoded at full DPI before layout | Thumbnail-first decode |
| 3 | PDF | Uses a temp `.tmp.png` on disk | Use `io.BytesIO` |
| 4 | Manifest | No validation that manifest covers all images in folder | Add `--strict-manifest` flag to error on missing files |
| 5 | Ordering | No support for duplicate entries in manifest (same image twice) | Allow repeats by loading by path index |
| 6 | CLI | `--dpi` not exposed as CLI override (only in INI) | Add `--dpi` flag that trumps INI |

---

## Changelog

| Date | Change |
|---|---|
| 2026-03-13 | Initial version — 60cm PDF, greedy row layout, rotation, rounded corners, white border |
| 2026-03-13 | Extracted all tuneable params to `collage.ini`; added `Config` dataclass; min/max per row via uniform scaling; `row_gap_px`, `transparent_background` |
| 2026-03-13 | Fixed rotation overlap: `arrange_rows` uses worst-case rotated width; `build_canvas` spaces by actual rotated bbox; added `overlap_tolerance_px` |
| 2026-03-13 | Added three ordering modes (`filename`, `shuffle`, `manifest`); `--order` and `--seed` CLI flags; `--save-manifest` export; auto-detect manifest; `order` and `shuffle_seed` in INI |

---

*Update this file whenever the script's interface, constants, architecture, or known issues change.*
