# Collage Generator v2 — Project Context

> **Claude Code context file.** Load this at session start to resume work on this project.
> Keep updated after every significant change.
> See also: CLAUDE.md for v1 context and design history.

---

## Project Overview

v2 of the collage generator. Identical to v1 in all respects **except the row
layout algorithm**, which is replaced with a **dynamic-programming (DP) row-breaker**
that minimises scale distortion globally across all rows.

**Problem with v1:** fixed image count per row → brutal rescaling when aspect
ratios don't match the row budget.

**v2 fix:** images are loaded once at a reference height. The DP solver finds
the globally optimal set of row breaks that minimises the total squared deviation
of each row's correction scale from 1.0. Typical correction: ±3–8%.

**Status:** final / stable. Default parameters locked in March 2026.

---

## File Structure

```
project/
├── collage_generator2.py  # v2 main script
├── collage2.ini           # v2 parameters (final defaults)
├── order.txt              # shared manifest (same format as v1)
├── CLAUDE2.md             # ← this file
│
├── collage_generator.py   # v1 (fixed-count layout, kept for reference)
├── collage.ini            # v1 parameters
└── CLAUDE.md              # v1 context
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

Python: **3.9+** (uses `Optional[X]` from `typing`, no `X | Y` union syntax)

---

## Usage

```bash
# Basic (uses defaults from collage2.ini: shuffle, seed=42)
python collage_generator2.py ./photos output.pdf

# Override ordering
python collage_generator2.py ./photos output.pdf --order filename
python collage_generator2.py ./photos output.pdf --order manifest
python collage_generator2.py ./photos output.pdf --order shuffle --seed 7

# Generate/update order.txt from current run
python collage_generator2.py ./photos output.pdf --save-manifest

# Debug: stamp filename + true scale on every image
python collage_generator2.py ./photos proof.pdf --label-files

# Custom config
python collage_generator2.py ./photos output.pdf --config /path/to/custom.ini
```

### CLI Arguments

| Argument | Default | Description |
|---|---|---|
| `folder` | required | Source image folder |
| `output` | `collage.pdf` | Output PDF |
| `--config` | `./collage2.ini` | INI path |
| `--order` | *(from INI)* | `filename` / `shuffle` / `manifest` — overrides INI |
| `--seed` | *(from INI)* | RNG seed for shuffle — overrides INI `shuffle_seed` |
| `--save-manifest` | off | Write resolved order to `order.txt` in source folder |
| `--label-files` | off | **[DEBUG]** Stamp filename + true scale on each image |

---

## Final Default Parameters (collage2.ini)

```ini
[canvas]
width_cm = 60.0
dpi = 150
background_color = 255,255,255
transparent_background = false

[layout]
order = shuffle
shuffle_seed = 42
target_row_height_px = 420
min_row_height_px = 360
max_row_height_px = 620
min_images_per_row = 0
max_images_per_row = 0
padding_px = 4
row_gap_px = 2

[dp_cost]
scale_penalty_weight  = 1.0
height_penalty_weight = 1.0
widows_penalty        = 2.0

[decoration]
border_px = 0
corner_radius_px = 5
rotation_max_deg = 0
overlap_tolerance_px = 3
```

---

## collage2.ini Full Reference

### [canvas]

| Key | Default | Description |
|---|---|---|
| `width_cm` | `60.0` | Physical paper width in cm; height is auto-calculated |
| `dpi` | `150` | Render resolution. 150 = preview, 300 = print quality |
| `background_color` | `255,255,255` | R,G,B canvas fill; ignored when `transparent_background = true` |
| `transparent_background` | `false` | RGBA canvas + `mask="auto"` in PDF for layout tool compositing |

### [layout]

| Key | Default | Description |
|---|---|---|
| `order` | `shuffle` | `filename` / `shuffle` / `manifest` |
| `shuffle_seed` | `42` | RNG seed; `-1` = different order every run |
| `target_row_height_px` | `420` | Reference load height; DP corrects ≈ ±5% |
| `min_row_height_px` | `360` | Reject rows shorter than this |
| `max_row_height_px` | `620` | Reject rows taller than this |
| `min_images_per_row` | `0` | `0` = unconstrained (recommended) |
| `max_images_per_row` | `0` | `0` = unconstrained (recommended) |
| `padding_px` | `4` | Horizontal gap between images |
| `row_gap_px` | `2` | Vertical gap between rows |

### [dp_cost]

| Key | Default | Description |
|---|---|---|
| `scale_penalty_weight` | `1.0` | Penalises scale deviation from 1.0 |
| `height_penalty_weight` | `1.0` | Penalises height deviation from target |
| `widows_penalty` | `2.0` | Extra cost for sole image in last row; `0.0` to allow |

### [decoration]

| Key | Default | Description |
|---|---|---|
| `border_px` | `0` | White border per image; `0` = disabled |
| `corner_radius_px` | `5` | Rounded corner radius; `0` = sharp corners |
| `rotation_max_deg` | `0` | Max ±random rotation; `0` = no rotation |
| `overlap_tolerance_px` | `3` | Hard floor on gap between adjacent bounding boxes |

### `target_row_height_px` sizing guide (150 DPI, 60 cm canvas ≈ 3543 px wide)

| Value | Approx images/row (3:2 landscape) |
|---|---|
| 300 px | 11–13 |
| 400 px | 8–9 |
| 420 px | 7–9 |
| 480 px | 6–8 |
| 600 px | 5–6 |
| 750 px | 4–5 |

### Tuning `[dp_cost]`

| Goal | Adjustment |
|---|---|
| Images closer to natural size | Increase `scale_penalty_weight` |
| More uniform row heights | Increase `height_penalty_weight` |
| Allow lone last image | Set `widows_penalty = 0` |
| Height uniformity over scale | `height_penalty_weight >> scale_penalty_weight` |
| More compositional flexibility | Decrease both weights |

---

## The DP Layout Algorithm

### Core idea

Treat row-breaking as a 1D optimisation problem, identical in structure to
Knuth-Plass paragraph justification (used in TeX).

**State:** `cost[i]` = minimum total layout cost to place `images[0..i-1]`

**Transition:** for every `j < i`, consider placing `images[j..i-1]` in one row:

```
scale s = (canvas_width - padding*(count-1)) / sum(rotated_widths[j..i-1])

row_height = s * target_row_height_px

cost contribution =
    scale_penalty_weight  * (s - 1.0)²
  + height_penalty_weight * ((row_height - target) / target)²
  [+ widows_penalty  if  i == N  and  count == 1]

cost[i] = min over all valid j of  cost[j] + contribution(j, i)
```

Candidates are rejected if `row_height` falls outside `[min, max]_row_height_px`
or image count violates `[min, max]_images_per_row`.

**Backtrack** from `cost[N]` using `split[i]` pointers to recover row breaks.

**Complexity:** O(N²) time, O(N) space — fast for up to ~1000 images.

### Rotation-aware widths

The DP uses worst-case rotated bounding-box widths
(`w·cos θ_max + h·sin θ_max`) for scale computation, identical to v1.
With `rotation_max_deg = 0` (the default) this reduces to the natural width.

### Fallback

If no valid solution exists within the height bounds, the script falls back
to a greedy width-based row-filler and prints a warning. To avoid this,
widen `[min, max]_row_height_px`.

---

## Architecture

### Pipeline

```
load_config(ini_path)
    └── configparser → Config dataclass

resolve_order(folder, cfg, cli_order, cli_seed)
    └── filename  → sorted(discover_images())
    └── shuffle   → sorted then rng.shuffle()  [default: seed=42]
    └── manifest  → read_manifest() parses order.txt
    └── auto-detect: switches to manifest if order.txt present

[optional] write_manifest(folder, paths, mode, seed)

load_images(paths, cfg)
    └── Resize each image to target_row_height_px
    └── add_border(), round_corners()
    └── Store _src_name, _orig_h on each Image object for label pass

dp_break_rows(images, cfg, canvas_w)         ← KEY DIFFERENCE vs v1
    └── Pre-compute rot_widths[]
    └── DP forward pass: fill cost[], split[]
    └── Backtrack: recover break indices
    └── Resize each row with correction scale s
    └── Propagate _src_name, _orig_h through resize()

[optional] stamp_scale_labels(rows, cfg)     ← --label-files only
    └── For each image: true_scale = img.height / img._orig_h
    └── stamp_filename(img, name, scale)      ← single pass, bottom bar

build_canvas(rows, canvas_w, cfg, rng)
    └── Pass 1: pre-rotate (no-op when rotation_max_deg=0), measure height
    └── Pass 2: composite with rotated-width spacing

save_pdf(collage, output_path, cfg)
    └── reportlab Canvas at physical cm dimensions
    └── tmp PNG → drawImage → remove tmp
```

### Key functions

| Function | Purpose |
|---|---|
| `load_config(ini_path)` | Parse and validate collage2.ini → `Config` |
| `discover_images(folder)` | Find all supported images (unsorted) |
| `read_manifest(folder)` | Parse `order.txt` → `list[Path]` |
| `resolve_order(...)` | Central ordering logic; returns `(paths, mode, seed)` |
| `write_manifest(...)` | Serialise resolved order to `order.txt` |
| `load_images(paths, cfg)` | Load, resize to reference height, decorate |
| `dp_break_rows(images, cfg, canvas_w)` | Main DP solver; returns scaled rows |
| `_row_scale(widths, canvas_w, padding)` | Scale factor for a candidate row |
| `_rot_expanded_width(w, h, max_rad)` | Worst-case rotated bbox width |
| `_greedy_fallback(images, cfg, canvas_w)` | Fallback if DP finds no solution |
| `_scale_row(chunk, canvas_w, pad, max_rad)` | Uniform-scale one row chunk |
| `stamp_scale_labels(rows, cfg)` | Post-DP debug label pass (--label-files) |
| `stamp_filename(img, name, scale)` | Render bottom label bar on one image |
| `build_canvas(rows, canvas_w, cfg, rng)` | Two-pass composite |
| `save_pdf(collage, output_path, cfg)` | reportlab PDF export |

### Custom attribute propagation

PIL's `Image.resize()` returns a plain new object — custom Python attributes
are silently dropped. `_src_name` and `_orig_h` must be copied explicitly
after every resize. This is done in:
- `dp_break_rows()` — main DP resize loop
- `_scale_row()` — greedy fallback resize loop

If future code adds another resize step, it must propagate these attributes.

---

## `--label-files` debug mode

Stamps a semi-transparent bar at the **bottom** of every image containing:

- **Line 1:** filename (white)
- **Line 2:** true scale factor, colour-coded:

| Colour | Range | Meaning |
|---|---|---|
| 🟢 Green | ±5% of original | Minimal distortion |
| 🟡 Yellow | ±15% of original | Acceptable resize |
| 🔴 Red | >±15% of original | Significant resize — check source resolution |

**True scale** = `final_display_height_px / original_file_height_px`

Example: a 4000 px source displayed at 420 px → `x0.105` (green).

Stamping is a **single pass** in `stamp_scale_labels()` after `dp_break_rows()`
— not at load time — because the true scale is only known once the DP has
assigned and resized each row.

---

## Key Differences from v1

| Aspect | v1 | v2 |
|---|---|---|
| Layout algorithm | Fixed count + uniform scale | DP optimal row-breaking |
| Scale distortion | Unbounded | Minimised globally (typically ±5%) |
| Images per row | Strictly controlled | Naturally variable (soft bounds) |
| Control knobs | `min/max_images_per_row` | `target/min/max_row_height_px` + cost weights |
| Complexity | O(N) | O(N²) |
| `[dp_cost]` section | Not present | Required |
| Fallback | None | Greedy if constraints infeasible |
| Debug labels | Filename only | Filename + true scale, colour-coded |
| Default order | `filename` | `shuffle` (seed 42) |
| Default rotation | 8° | 0° |
| Default corners | 10 px | 5 px |

---

## Known Limitations & Potential Improvements

| # | Area | Issue | Suggested Fix |
|---|---|---|---|
| 1 | DP | O(N²) — slow beyond ~1000 images | Add `max_lookahead` window to limit j range |
| 2 | DP | Images resized twice (load + correction) | Store originals, resize once post-DP |
| 3 | Fallback | Greedy fallback ignores cost weights | Re-implement using same cost function |
| 4 | PDF | Temp `.tmp.png` written to disk | Use `io.BytesIO` |
| 5 | Last row | `widows_penalty` only guards 1-image rows | Add penalty for last row significantly narrower |
| 6 | CLI | No `--dpi` override (only in INI) | Add `--dpi` flag |
| 7 | Config | No validation of `min > max` inversions | Add sanity checks in `load_config()` |
| 8 | Labels | Font fallback chain may yield tiny default font | Bundle a minimal TTF or use freetype directly |

---

## Changelog

| Date | Change |
|---|---|
| 2026-03-13 | v2 initial — DP row-breaking, `[dp_cost]` section, scale diagnostics, greedy fallback, Python 3.9 compat |
| 2026-03-13 | `--label-files`: two-line bottom bar (filename + true scale, colour-coded) |
| 2026-03-13 | Fixed label double-stamp: removed first-pass stamp from `load_images()`; single pass in `stamp_scale_labels()` only |
| 2026-03-13 | Fixed `_orig_h = None` / `"?"` scale: propagate `_src_name` and `_orig_h` through all `resize()` calls |
| 2026-03-13 | Fixed label position: bar anchored to bottom edge |
| 2026-03-13 | **Final defaults locked:** `rotation_max_deg=0`, `corner_radius_px=5`, `border_px=0`, `shuffle_seed=42`, `order=shuffle`, `target_row_height_px=420` |

---

*Update this file whenever the script's interface, algorithm, or known issues change.*
