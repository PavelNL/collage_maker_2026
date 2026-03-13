# Collage Generator v2 — Project Context

> **Claude Code context file.** Load this at session start to resume work on this project.
> Keep updated after every significant change.
> See also: CLAUDE.md for v1 context and design history.

---

## Project Overview

v2 of the collage generator.  Identical to v1 in all respects **except the row
layout algorithm**, which is replaced with a **dynamic-programming (DP) row-breaker**
that minimises scale distortion globally across all rows.

**Problem with v1:** fixed image count per row → brutal rescaling when aspect
ratios don't match the row budget.

**v2 fix:** images are loaded once at a reference height.  The DP solver finds
the globally optimal set of row breaks that minimises the total squared deviation
of each row's correction scale from 1.0.  Typical correction: ±3–8%.

---

## File Structure

```
project/
├── collage_generator2.py  # v2 main script
├── collage2.ini           # v2 parameters
├── order.txt              # shared manifest (same format as v1)
├── CLAUDE2.md             # ← this file
│
├── collage_generator.py   # v1 (fixed-count layout)
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

Python: **3.9+** (no `X | Y` union syntax; uses `Optional[X]` from `typing`)

---

## Usage

```bash
# Basic
python collage_generator2.py ./photos output.pdf

# With options
python collage_generator2.py ./photos output.pdf --order shuffle --seed 42
python collage_generator2.py ./photos output.pdf --order manifest
python collage_generator2.py ./photos output.pdf --save-manifest
python collage_generator2.py ./photos proof.pdf  --label-files
python collage_generator2.py ./photos output.pdf --config /path/to/custom.ini
```

### CLI Arguments

| Argument | Default | Description |
|---|---|---|
| `folder` | required | Source image folder |
| `output` | `collage.pdf` | Output PDF |
| `--config` | `./collage2.ini` | INI path |
| `--order` | *(from INI)* | `filename` / `shuffle` / `manifest` |
| `--seed` | *(from INI)* | RNG seed for shuffle |
| `--save-manifest` | off | Write `order.txt` after resolving order |
| `--label-files` | off | **[DEBUG]** Stamp filename on each photo |

---

## The DP Layout Algorithm

### Core idea

Treat row-breaking as a 1D optimisation problem, identical in structure to
Knuth-Plass paragraph justification (used in TeX).

**State:** `cost[i]` = minimum total layout cost to place `images[0..i-1]`

**Transition:** for every `j < i`, consider placing `images[j..i-1]` in a
single row:

```
scale s = (canvas_width - padding*(i-j-1)) / sum(rotated_widths[j..i-1])

row_height = s * target_row_height_px

cost contribution =
    scale_penalty_weight  * (s - 1.0)²
  + height_penalty_weight * ((row_height - target) / target)²
  [+ widows_penalty  if  i == N  and  i-j == 1]

cost[i] = min over all valid j of  cost[j] + contribution(j, i)
```

Reject candidates where `row_height` falls outside `[min_row_height_px,
max_row_height_px]` or image count violates `[min, max]_images_per_row`.

**Backtrack** from `cost[N]` using `split[i]` pointers to recover row breaks.

**Complexity:** O(N²) time, O(N) space — fast even for 500+ images.

### Rotation-aware widths

The DP uses worst-case rotated bounding-box widths
(`w·cos θ_max + h·sin θ_max`) for scale computation, identical to v1.
This guarantees no overlap after random rotation is applied in `build_canvas()`.

### Fallback

If no valid solution exists within the height bounds (e.g. constraints are too
tight), the script falls back to a simple greedy width-based row-filler and
prints a warning.  To avoid the fallback: widen `[min, max]_row_height_px`.

---

## collage2.ini Reference

```ini
[canvas]
width_cm = 60.0
dpi = 150
background_color = 255,255,255
transparent_background = false

[layout]
order = filename              # filename | shuffle | manifest
shuffle_seed = -1             # -1 = true random
target_row_height_px = 480    # reference load height; DP corrects ≈ ±5%
min_row_height_px = 360       # reject rows shorter than this
max_row_height_px = 620       # reject rows taller than this
min_images_per_row = 0        # 0 = unconstrained (recommended)
max_images_per_row = 0        # 0 = unconstrained (recommended)
padding_px = 4
row_gap_px = 2

[dp_cost]
scale_penalty_weight  = 1.0   # penalise scale deviation from 1.0
height_penalty_weight = 1.0   # penalise height deviation from target
widows_penalty        = 2.0   # extra cost for sole image in last row

[decoration]
border_px = 0
corner_radius_px = 10
rotation_max_deg = 8
overlap_tolerance_px = 3
```

### Tuning `[dp_cost]`

| Goal | Adjustment |
|---|---|
| Images stay closer to natural size | Increase `scale_penalty_weight` |
| More uniform row heights | Increase `height_penalty_weight` |
| Allow lone last image | Set `widows_penalty = 0` |
| Prioritise height uniformity over scale | `height_penalty_weight >> scale_penalty_weight` |
| Allow flexible compositions | Decrease both weights |

### `target_row_height_px` sizing guide (at 150 DPI, 60cm canvas ≈ 3543px wide)

| Value | Approx images/row (3:2 landscape) |
|---|---|
| 300 px | 11–13 |
| 400 px | 8–9 |
| 480 px | 6–8 |
| 600 px | 5–6 |
| 750 px | 4–5 |

---

## Architecture

### Pipeline

```
load_config(ini_path)           → Config
resolve_order(folder, cfg, ...)  → (paths, mode, seed)
[write_manifest()]               optional
load_images(paths, cfg)          → list[Image]   ← loaded at target_row_height_px
dp_break_rows(images, cfg, w)    → list[list[Image]]  ← THE KEY DIFFERENCE vs v1
build_canvas(rows, w, cfg, rng)  → Image          ← identical to v1
save_pdf(collage, path, cfg)                       ← identical to v1
```

### Functions unique to v2

| Function | Purpose |
|---|---|
| `dp_break_rows(images, cfg, canvas_w)` | Main DP solver; returns scaled rows |
| `_row_scale(widths, canvas_w, padding)` | Compute scale factor for a candidate row |
| `_rot_expanded_width(w, h, max_rad)` | Worst-case rotated bbox width |
| `_greedy_fallback(images, cfg, canvas_w)` | Fallback if DP finds no valid solution |
| `_scale_row(chunk, canvas_w, pad, max_rad)` | Uniform-scale a single row chunk |

### Functions shared with v1 (identical implementations)

`resolve_order`, `read_manifest`, `write_manifest`, `discover_images`,
`add_border`, `round_corners`, `stamp_filename`, `build_canvas`, `save_pdf`

---

## Key Differences from v1

| Aspect | v1 | v2 |
|---|---|---|
| Layout algorithm | Fixed count + uniform scale | DP optimal row-breaking |
| Scale distortion | Unbounded (can be severe) | Minimised globally (typically ±5%) |
| Images per row | Strictly controlled | Naturally variable (soft bounds) |
| Control knobs | `min/max_images_per_row` | `target/min/max_row_height_px` + cost weights |
| Complexity | O(N) | O(N²) |
| `[dp_cost]` section | Not present | Required |
| Fallback | None | Greedy fallback if constraints infeasible |
| Diagnostics | Basic | Prints per-row scale stats (min/max/mean) |

---

## Known Limitations & Potential Improvements

| # | Area | Issue | Suggested Fix |
|---|---|---|---|
| 1 | DP | O(N²) is fine to ~1000 images; beyond that, slow | Add a `max_lookahead` window to limit j range |
| 2 | DP | All images are resized twice (load + DP correction) | Store original PIL images, resize once post-DP |
| 3 | Fallback | Greedy fallback ignores cost weights | Re-implement fallback using same cost function |
| 4 | PDF | Temp `.tmp.png` written to disk | Use `io.BytesIO` |
| 5 | Last row | `widows_penalty` only guards single-image last rows | Add penalty for last row significantly shorter than others |
| 6 | CLI | `--dpi` not exposed as CLI override | Add `--dpi` flag |
| 7 | Config | No validation of `min > max` inversions | Add sanity checks in `load_config()` |

---

## Changelog

| Date | Change |
|---|---|
| 2026-03-13 | v2 initial — DP row-breaking, `[dp_cost]` section, scale diagnostics, greedy fallback, full Python 3.9 compatibility |

---

*Update this file whenever the script's interface, algorithm, or known issues change.*
