
A straightforward, deterministic correction tool is exactly the right approach.**CLI:**
```bash
pip3 install Pillow numpy
python3 adjust_image.py --input photo.jpg --output photo_print.png --profile corrections.json [--debug]
```

**Profile keys** (all optional — omit any key to leave it at its neutral value):

| Key | Neutral | Effect |
|---|---|---|
| `brightness` | `0` | Global additive offset, –255 … +255 |
| `contrast` | `1.0` | Multiplier around midpoint 128 |
| `saturation` | `1.0` | HSV saturation multiplier |
| `hue_shift` | `0` | Hue rotation in degrees, –180 … +180 |
| `shadows` | `0` | Additive lift/push for tones 0–85 |
| `midtones` | `0` | Additive lift/push for tones 86–170 |
| `highlights` | `0` | Additive lift/push for tones 171–255 |
| `r_offset` / `g_offset` / `b_offset` | `0` | Per-channel additive shift |
| `r_gain` / `g_gain` / `b_gain` | `1.0` | Per-channel multiplicative gain |

The debug strip highlights changed parameters in red with a `●` marker and shows neutral ones in grey, so you can verify at a glance exactly what was applied before sending to the shop. The neutral profile output confirms the no-op case works correctly.
