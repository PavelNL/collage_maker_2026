Great idea — a self-contained Python CLI tool that does two things:

1. **Generate** a calibration target sheet (known patch values + your reference image crop, with labelled deviation annotations)
2. **Apply** the derived corrections to any image as a preparation step for printing

Let me check the skills first.No image-processing skill, so I'll build this from scratch using Pillow. Let me write the full tool:Grey key "Green" collides with the `G` prefix filter. Quick fix:All three modes working cleanly. Copy to outputs:The tool has three modes and only needs `pip install Pillow numpy`.

**Workflow in practice:**

```bash
# Step 1 — generate the target sheet (print this, nothing else)
python print_calibration.py generate --ref your_photo.jpg --out target.png

# Step 2 — photograph/scan the printed target, then measure it
python print_calibration.py measure \
    --scan scanned_target.jpg \
    --target target.png \
    --out corrections.json

# Step 3 — apply the derived corrections to any image before printing
python print_calibration.py apply \
    --image your_photo.jpg \
    --corrections corrections.json \
    --out your_photo_ready.png
```

**What it does at each step:**

`generate` builds a target sheet containing a 12-step greyscale ramp, 17 named colour patches (CMYK primaries, neutrals, skin tones, memory colours), and a centre crop from your own reference image — all labelled with their exact target RGB values. This is what you print and hand-compare under both light sources.

`measure` takes your scan of the printed target, samples the centre 60% of each patch (to avoid edge contamination), computes the ΔE deviation per patch, derives a correction profile with per-zone curve adjustments (shadows/midtones/highlights independently), and writes both a `corrections.json` and an annotated deviation report image with colour-coded ΔE badges (green/amber/red).

`apply` loads the correction profile and applies it non-destructively — global per-channel offset, then a blended zone LUT, then an optional saturation boost if the measurement indicated saturation compression. It also saves a before/after comparison strip.

The `corrections.json` is reusable — once you have a good profile for a particular printer/paper/ink combination, you can run `apply` on any number of images without reprinting the target.