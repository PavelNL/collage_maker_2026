# CLAUDE2 — Project Memory & Specification
## Print Preparation Image Correction Tool (`adjust_image.py`)

---

## 1. Project Origin & Context

The tool was developed in the context of preparing JPG, PNG, and PDF files
for printing at a shop that provides **no colour calibration or ICC profile
utility**. The target is prints that look good under **both natural daylight
(near a window, approx. D50–D65) and artificial indoor light (tungsten/LED)**.

The original discussion covered soft-proofing theory, ICC profiles, rendering
intents, and colour cast compensation. The user correctly rejected a
scanner-based empirical calibration approach (a "calibration target" printed
and scanned back) on the grounds that **the scanner introduces its own
errors**, invalidating any derived corrections. The accepted solution is a
**deterministic, human-specified correction profile** applied directly to the
source file.

---

## 2. Tool: `adjust_image.py`

### 2.1 Purpose

Apply a set of named, human-readable corrections to an image or PDF before
sending it to print. All corrections are specified in an external JSON profile
file. A debug mode appends a white annotation strip to the output showing
exactly which corrections were applied.

### 2.2 Dependencies

```
pip install Pillow numpy pymupdf
```

- `Pillow` — image I/O and compositing
- `numpy` — vectorised pixel arithmetic
- `pymupdf` (imported as `fitz`) — PDF rasterisation; **lazily imported**, only
  required when the input is a `.pdf` file. Image-only usage works without it.

**Python compatibility: 3.9 and above.** No 3.10+ syntax (`X | Y` union hints,
`match` statements, subscripted built-in generics in annotations). Uses
`Tuple` from `typing` module explicitly.

### 2.3 CLI Signature

```
python adjust_image.py \
    --input   <file>         # JPG, PNG, TIFF or PDF
    --output  <file>         # output path (same or different format)
    --profile <file.json>    # correction profile
    [--debug]                # append annotation strip
    [--dpi    <int>]         # PDF rasterisation DPI (default: 300)
```

### 2.4 Format Auto-Detection

The input file extension is inspected at runtime:

- `.pdf` — routes to `process_pdf()`
- all other extensions — routes to `process_image()` via Pillow

A warning is printed if the input is PDF but the output path does not end
in `.pdf`.

---

## 3. Correction Profile (JSON)

All keys are **optional**. Missing keys silently fall back to their neutral
(no-op) value. Unknown keys emit a `[warn]` and are ignored.

| Key          | Type  | Neutral | Range          | Effect                                      |
|--------------|-------|---------|----------------|---------------------------------------------|
| `brightness` | float | `0`     | -255 … +255    | Global additive offset to all channels      |
| `contrast`   | float | `1.0`   | 0.0 … 3.0      | Multiplier scaled around midpoint 128       |
| `saturation` | float | `1.0`   | 0.0 … 3.0      | HSV S-channel multiplier                    |
| `hue_shift`  | float | `0`     | -180 … +180    | HSV H-channel rotation in degrees           |
| `shadows`    | float | `0`     | -128 … +128    | Additive correction for tones 0–85          |
| `midtones`   | float | `0`     | -128 … +128    | Additive correction for tones 86–170        |
| `highlights` | float | `0`     | -128 … +128    | Additive correction for tones 171–255       |
| `r_offset`   | float | `0`     | -255 … +255    | Red channel additive offset                 |
| `g_offset`   | float | `0`     | -255 … +255    | Green channel additive offset               |
| `b_offset`   | float | `0`     | -255 … +255    | Blue channel additive offset                |
| `r_gain`     | float | `1.0`   | 0.0 … 3.0      | Red channel multiplicative gain             |
| `g_gain`     | float | `1.0`   | 0.0 … 3.0      | Green channel multiplicative gain           |
| `b_gain`     | float | `1.0`   | 0.0 … 3.0      | Blue channel multiplicative gain            |

Example profile:

```json
{
  "brightness":  -10,
  "contrast":    1.05,
  "saturation":  1.10,
  "hue_shift":   5,
  "shadows":     8,
  "highlights":  -5,
  "r_offset":    -5,
  "g_offset":    4,
  "b_offset":    10,
  "b_gain":      0.97
}
```

---

## 4. Correction Pipeline — Order of Operations

Operations are applied in this fixed order inside `apply_corrections()`.
Order matters: gain before offset, both before global brightness, zone
curve last before HSV operations.

```
1. Per-channel gain      arr[:,c] *= gain[c]
2. Per-channel offset    arr[:,c] += offset[c]
3. Global brightness     arr += brightness
4. Contrast              arr = (arr - 128) * contrast + 128
5. Zone curve LUT        shadows / midtones / highlights
6. Saturation            HSV S *= saturation   (skipped if == 1.0)
7. Hue shift             HSV H += hue_shift    (skipped if == 0.0)
```

All intermediate values are held as `float32`. Final clip to [0, 255] and
cast to `uint8` happens once at the end.

### Zone Curve Detail

The shadow/midtone/highlight corrections use cosine-blended zone weights
to avoid hard transitions at zone boundaries:

- Shadows:    tones 0–85 with cosine rolloff
- Midtones:   tones 86–170, residual weight after shadows and highlights
- Highlights: tones 171–255 with cosine rolloff

Each correction is additive. A 256-entry LUT is built once per call and
applied via NumPy vectorised indexing (`arr = lut[arr]`).

---

## 5. PDF Processing

### Decision: Option 2 — pymupdf (fitz) rasterisation + repack

Four options were evaluated:

| Option | Approach | Verdict |
|--------|----------|---------|
| 1 | pypdf + Pillow rasterise/repack | Acceptable, lower fidelity |
| **2** | **pymupdf rasterise + repack** | **Chosen — best rasterisation quality** |
| 3 | pypdf in-place image extraction | Complex, breaks on XObject PDFs |
| 4 | Ghostscript subprocess | Best fidelity but external dependency, non-portable |

### PDF Processing Steps (`process_pdf()`)

1. Open source PDF with `fitz.open()`
2. For each page:
   - Render to RGB pixmap: `page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)`
   - Convert pixmap samples to PIL Image via `Image.frombytes()`
   - Apply `apply_corrections()` — identical pipeline to image mode
   - If `--debug` and last page: append debug strip to the PIL image
   - Compute output page size in points: `px * 72 / dpi`
   - Insert corrected image as JPEG stream into new PDF page
3. Save output PDF with `deflate=True, garbage=4` (compact output)

### Output format

The output PDF is **raster** (each page is a JPEG-compressed bitmap).
This is correct and intended for photo-heavy print-ready files. It is not
suitable if the original PDF contains vector text or line art that must
remain vector.

### DPI

Default rasterisation DPI is **300**. User-overridable via `--dpi`.
- 150 dpi — fast preview pass
- 300 dpi — standard print quality (default)
- 600 dpi — high-fidelity / large format

Page dimensions in the output PDF are preserved in physical units (points)
so that a 300 dpi A4 page renders as A4 regardless of pixel count.

### Lazy import

`pymupdf` (`fitz`) is imported only when the input is detected as `.pdf`.
Image-only workflows have no dependency on pymupdf being installed.

---

## 6. Debug Strip

### Behaviour (decision recorded here)

**Only changed parameters are printed.** Default/neutral values are not shown.
If no parameters differ from their defaults, the strip shows:
`no corrections applied` in grey.

Changed parameters are printed in **red** (`(180, 60, 60)`).

### Layout

- White background strip appended **below** the image (or last PDF page)
- Top line: `Applied corrections:` label in grey
- Second line: space-separated `key: value` tokens for each changed parameter
- Values formatted as:
  - Integer offsets (`brightness`, `shadows`, `midtones`, `highlights`,
    `r_offset`, `g_offset`, `b_offset`, `hue_shift`): `+N` or `-N`
  - Multipliers (`contrast`, `saturation`, `r_gain`, `g_gain`, `b_gain`): `N.NNN`
- Strip height is dynamic (computed from font metrics, not a fixed constant)

### Earlier rejected design

An earlier version printed **all 13 parameters** in a fixed 5-column grid,
showing neutral values in grey and changed values in red. This was rejected
because it was visually noisy and made changed values hard to spot quickly.

---

## 7. Image Output Format

- PNG/TIFF output: saved with `dpi=(300, 300)` metadata
- JPEG output: `quality=95, subsampling=0` (4:4:4, no chroma subsampling)
- PDF output: JPEG streams at `quality=95, subsampling=0` per page,
  outer PDF saved with `deflate=True, garbage=4`

---

## 8. Key Design Decisions (Summary)

| Decision | Choice | Reason |
|----------|--------|--------|
| Correction specification | Human-edited JSON profile | Scanner-based auto-measurement rejected as it introduces scanner errors |
| Profile missing keys | Fall back to neutral silently | Allows minimal profiles; user only specifies what they want to change |
| Unknown profile keys | Warn and ignore | Fail-safe; future-proof |
| Operation order | Fixed (gain→offset→brightness→contrast→zone→sat→hue) | Mathematically consistent; gain before offset prevents offset being amplified |
| Zone blending | Cosine ramps | Avoids visible tonal discontinuities at zone boundaries |
| HSV operations | Skip if at neutral value | Avoids unnecessary round-trip conversion noise |
| PDF engine | pymupdf (fitz) | Best rasterisation quality; handles fonts, transparency, colour spaces |
| PDF import | Lazy (only on PDF input) | No pymupdf dependency for image-only usage |
| Debug strip content | Changed parameters only | Rejected: all-parameters grid; accepted: changed-only flat list |
| Debug strip placement | Below image / last PDF page | Non-destructive; easy to crop before final send |
| Python version target | 3.9+ | No 3.10+ syntax; uses `typing.Tuple` not `tuple[...]` |

---

## 9. Recommended Empirical Workflow

Since no shop calibration is available, the intended workflow is:

1. **Print a test image** (small crop of the target photo, or a known reference)
   with a neutral profile (`{}`)
2. **Visually assess** the print under both daylight and artificial light:
   - Is it too warm/cool? → adjust `r_offset`/`b_offset` or `hue_shift`
   - Too dark/bright overall? → adjust `brightness`
   - Shadows blocking up? → adjust `shadows`
   - Highlights blown? → adjust `highlights`
   - Colours look flat? → raise `saturation`
3. **Edit the profile JSON** with derived values
4. **Reprint the test image** with `--debug` to confirm corrections at a glance
5. **Apply the same profile** to the full production file

The profile JSON is the persistent record of corrections for a given
printer/paper/ink combination and can be reused for future jobs on the
same setup.

---

## 10. File Inventory

| File | Description |
|------|-------------|
| `adjust_image.py` | Main tool — current production version |
| `example_profile.json` | Example correction profile with typical print values |
| `CLAUDE2.md` | This document |
