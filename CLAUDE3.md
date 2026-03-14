# Collage Generator v3 — Project Context

> **Claude Code context file.** Load this at session start to resume work on this project.
> Keep updated after every significant change.
> See also: CLAUDE2.md for v2 context and full algorithm history.

---

## Project Overview

v3 of the collage generator. Identical to v2 in every respect **except how
layout and decoration dimensions are expressed**: all pixel-based parameters
have been replaced with **physical millimetre values** in the INI. The script
converts mm → px at startup using the configured DPI.

**Problem with v2:** pixel parameters were implicitly tied to 150 DPI. Doubling
to 300 DPI without changing the INI produced images half the intended physical
size, because e.g. `target_row_height_px=420` is only ~71mm at 150 DPI but
only ~35mm at 300 DPI.

**v3 fix:** parameters like `target_row_height_mm = 71.0` are physically
meaningful. At 150 DPI → 420px; at 300 DPI → 840px. The physical layout is
identical at any DPI. **To switch from preview to print: change `dpi` only.**

**Status:** current / final.

---

## File Structure

```
project/
├── collage_generator3.py  # v3 main script  ← use this
├── collage3.ini           # v3 parameters (mm-based, DPI-agnostic)
├── order.txt              # shared manifest (same format as v1/v2)
├── CLAUDE3.md             # ← this file
│
├── collage_generator2.py  # v2 (pixel-based, 150 DPI assumed)
├── collage2.ini
├── CLAUDE2.md
│
├── collage_generator.py   # v1 (fixed-count layout)
├── collage.ini
└── CLAUDE.md
```

---

## Dependencies

```
Pillow       # Image loading, transformation, compositing
reportlab    # PDF rendering at physical dimensions
```

```bash
pip install Pillow reportlab
```

Python: **3.9+**

---

## Usage

```bash
# Preview quality (150 DPI, default)
python collage_generator3.py ./photos output.pdf

# Print quality — change dpi in collage3.ini to 300, then:
python collage_generator3.py ./photos output_print.pdf

# Ordering
python collage_generator3.py ./photos output.pdf --order filename
python collage_generator3.py ./photos output.pdf --order manifest
python collage_generator3.py ./photos output.pdf --order shuffle --seed 7

# Manifest
python collage_generator3.py ./photos output.pdf --save-manifest

# Debug labels
python collage_generator3.py ./photos proof.pdf --label-files

# Custom config
python collage_generator3.py ./photos output.pdf --config /path/to/other.ini
```

### CLI Arguments

| Argument | Default | Description |
|---|---|---|
| `folder` | required | Source image folder |
| `output` | `collage.pdf` | Output PDF |
| `--config` | `./collage3.ini` | INI path |
| `--order` | *(from INI)* | `filename` / `shuffle` / `manifest` |
| `--seed` | *(from INI)* | RNG seed for shuffle — overrides `shuffle_seed` |
| `--save-manifest` | off | Write resolved order to `order.txt` |
| `--label-files` | off | **[DEBUG]** Stamp filename + true scale on each image |

---

## Switching DPI — The Only Change Needed

```ini
# collage3.ini

[canvas]
dpi = 150    # preview  →  change to 300 for print
```

Everything else stays the same. The startup log confirms the conversion:

```
⚙   DPI=300  canvas=60.0cm × auto  (7087px wide)
    row height target=71.0mm → 840px  [55.0–95.0mm = 651–1122px]
    padding=0.7mm=8px  row_gap=0.4mm=5px  corners=0.85mm=10px
```

---

## collage3.ini Full Reference

### [canvas]

| Key | Default | Description |
|---|---|---|
| `width_cm` | `60.0` | Physical canvas width in cm; height is auto |
| `dpi` | `150` | **The only value to change for quality.** 150=preview, 300=print |
| `background_color` | `255,255,255` | R,G,B fill; ignored if `transparent_background=true` |
| `transparent_background` | `false` | RGBA canvas for layout tool compositing |

### [layout]

| Key | Default | Description |
|---|---|---|
| `order` | `shuffle` | `filename` / `shuffle` / `manifest` |
| `shuffle_seed` | `42` | RNG seed; `-1` = different every run |
| `target_row_height_mm` | `71.0` | Ideal row height in mm |
| `min_row_height_mm` | `55.0` | DP rejects rows shorter than this |
| `max_row_height_mm` | `95.0` | DP rejects rows taller than this |
| `min_images_per_row` | `0` | `0` = unconstrained (recommended) |
| `max_images_per_row` | `0` | `0` = unconstrained (recommended) |
| `padding_mm` | `0.7` | Horizontal gap between images |
| `row_gap_mm` | `0.4` | Vertical gap between rows |

### [dp_cost]

| Key | Default | Description |
|---|---|---|
| `scale_penalty_weight` | `1.0` | Penalises scale deviation from 1.0 |
| `height_penalty_weight` | `1.0` | Penalises height deviation from target |
| `widows_penalty` | `2.0` | Extra cost for sole image in last row |

### [decoration]

| Key | Default | Description |
|---|---|---|
| `border_mm` | `0.0` | White border per image; `0.0` = disabled |
| `corner_radius_mm` | `0.85` | Rounded corners; `0.0` = sharp |
| `rotation_max_deg` | `0.0` | Max ±random rotation; `0.0` = none |
| `overlap_tolerance_mm` | `0.5` | Hard floor on gap between rotated bboxes |

### Physical sizing guide (60cm canvas)

| `target_row_height_mm` | px @ 150dpi | px @ 300dpi | Approx imgs/row (3:2) |
|---|---|---|---|
| 50 mm | 295 px | 591 px | 11–13 |
| 60 mm | 354 px | 709 px | 9–11 |
| 71 mm | 420 px | 840 px | 7–9 |
| 85 mm | 502 px | 1004 px | 6–7 |
| 100 mm | 591 px | 1181 px | 5–6 |
| 120 mm | 709 px | 1417 px | 4–5 |

---

## Architecture

### Pipeline

```
load_config(ini_path)
    └── Read mm values from INI
    └── Compute all _px fields once: px = int(mm / 25.4 * dpi)
    └── Rest of pipeline uses cfg.*_px — never calls mm_to_px again

resolve_order(folder, cfg, cli_order, cli_seed)
    └── filename / shuffle / manifest  (identical to v1/v2)

[optional] write_manifest(folder, paths, mode, seed)

load_images(paths, cfg)
    └── Resize to cfg.target_row_height_px
    └── add_border(cfg.border_px), round_corners(cfg.corner_radius_px)
    └── Store _src_name, _orig_h per image

dp_break_rows(images, cfg, canvas_w)
    └── Uses cfg.*_px fields (already converted)
    └── _scale_row() propagates _src_name, _orig_h through resize()

[optional] stamp_scale_labels(rows)
    └── true_scale = img.height / img._orig_h
    └── stamp_filename(img, name, scale) → bottom label bar

build_canvas(rows, canvas_w, cfg, rng)
    └── Two-pass composite (identical to v1/v2)

save_pdf(collage, output_path, cfg)
    └── Output path suffix includes @{dpi}dpi in console log
```

### Unit conversion — single point

All mm → px conversion happens **exclusively in `load_config()`**:

```python
target_row_height_px = mm_to_px(target_mm, dpi)   # int(mm / 25.4 * dpi)
```

The `Config` dataclass carries both the original mm values (for display/logging)
and the derived px values (for all computation). No other function calls
`mm_to_px()`. This is the critical design constraint — if you add a new
mm-based parameter, convert it in `load_config()` and store both fields.

### Custom attribute propagation

`PIL.Image.resize()` drops Python attributes. `_src_name` and `_orig_h` are
copied in `_scale_row()` after every resize. Any future resize step must do
the same.

---

## Key Differences from v2

| Aspect | v2 | v3 |
|---|---|---|
| Layout units in INI | Pixels (DPI-tied) | Millimetres (DPI-agnostic) |
| To switch 150→300 DPI | Change `dpi` + 7 pixel params | Change `dpi` only |
| `Config` fields | `*_px` only | `*_mm` (from INI) + `*_px` (derived) |
| Conversion point | Scattered / implicit | Single: `load_config()` only |
| Startup log | Basic | Shows mm → px conversions for verification |
| Default INI file | `collage2.ini` | `collage3.ini` |

---

## Known Limitations & Potential Improvements

| # | Area | Issue | Suggested Fix |
|---|---|---|---|
| 1 | DP | O(N²) — slow beyond ~1000 images | Add `max_lookahead` window |
| 2 | DP | Images resized twice (load + correction) | Store originals, resize once post-DP |
| 3 | PDF | Temp `.tmp.png` written to disk | Use `io.BytesIO` |
| 4 | Labels | Font fallback may yield tiny default font | Bundle minimal TTF |
| 5 | Config | No validation of `min > max` inversions | Sanity checks in `load_config()` |
| 6 | Units | `width_cm` still in cm, not mm | Unify to mm for consistency (low priority) |

---

## Changelog

| Date | Change |
|---|---|
| 2026-03-13 | v1 — fixed-count greedy layout |
| 2026-03-13 | v2 — DP row-breaking; pixel-based INI at 150 DPI |
| 2026-03-13 | v2 — `--label-files`: bottom bar, filename + colour-coded true scale |
| 2026-03-13 | v2 — Fixed double stamp, `_orig_h` propagation, label position |
| 2026-03-13 | **v3** — All layout/decoration params in mm; single `load_config()` conversion; `dpi` is the only knob for quality switching; startup log shows mm→px for verification |

---

*Update this file whenever the script's interface, algorithm, or known issues change.*
