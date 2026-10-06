"""Turn downloaded CC0 PBR sets into the map textures both clients ship.

    python tools/hassault_process_textures.py <dir of unzipped ambientCG sets> [out_dir]

The sets are ambientCG's, licence CC0 1.0 (https://docs.ambientcg.com/license/):
free to use, change and ship, no attribution required (it is given anyway, in
`apps/web/public/maps/textures/README.md`). Only the 1K JPG maps are used.

Each kind becomes three 512 px WebP files in `apps/web/public/maps/textures/`:

- `<kind>_c.webp`  albedo, **normalised**: per channel, scaled in linear light so its
  mean is `DETAIL_MEAN` (0.6667), the mean the procedural tiles already have. The
  runtime multiplies by `DETAIL_GAIN` (1.5), so a surface averages back to the
  colour the map authored and the photograph only supplies the variation: stains,
  grain, the odd warmer patch. It does not repaint the map.
- `<kind>_n.webp`  tangent-space normal, OpenGL convention (+Y up).
- `<kind>_r.webp`  roughness, one channel.

Both clients read these: three.js fetches them, and the native client embeds them
with `include_bytes!`. WebP because the native client already decodes it (for the
operator's textures) and has no JPEG decoder; it is also the smaller file. Change the list here and in `glb-surfaces.json`, and run
this again; the output is deterministic.
"""

import os
import sys

import numpy as np
from PIL import Image

DETAIL_MEAN = 0.6667  # linear; glb-surfaces.json `detailMean`
SIZE = 512

# kind -> (ambientCG set, what it is for)
KINDS = {
    "sandstone": ("Ground093C", "rough cream stucco over sandstone"),
    "limewash": ("Plaster002", "pale limewashed plaster"),
    "paving": ("PavingStones150", "small cobbled paving"),
    "dunesand": ("Ground054", "wind-packed sand"),
    "cedar": ("Wood092", "weathered cedar"),
    "souk_crate": ("Planks037A", "plank crates"),
    "rust_iron": ("Metal053C", "rusted iron"),
    "burlap": ("Fabric081C", "coarse cloth"),
    "glaze": ("Tiles139", "glazed tile"),
}


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def normalise_albedo(img):
    """Per-channel linear mean to DETAIL_MEAN; contrast eased so nothing clips."""
    lin = srgb_to_linear(np.asarray(img.convert("RGB"), dtype=np.float64) / 255.0)
    mean = lin.reshape(-1, 3).mean(axis=0)
    out = lin * (DETAIL_MEAN / np.maximum(mean, 1e-4))
    # Ease contrast about the mean until the 99.8th percentile fits under 1.
    flat = out.reshape(-1, 3)
    for _ in range(40):
        peak = np.percentile(flat, 99.8, axis=0)
        if (peak <= 0.995).all():
            break
        k = np.minimum(
            1.0, (0.995 - DETAIL_MEAN) / np.maximum(peak - DETAIL_MEAN, 1e-4)
        )
        out = DETAIL_MEAN + (out - DETAIL_MEAN) * k.reshape(1, 1, 3)
        flat = out.reshape(-1, 3)
    # Easing moved the mean slightly: put it back, then clip the few stragglers.
    flat = out.reshape(-1, 3)
    out = out * (DETAIL_MEAN / np.maximum(flat.mean(axis=0), 1e-4))
    return np.clip(out, 0.0, 1.0)


def process(src_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    report = {}
    for kind, (set_id, _) in KINDS.items():
        base = os.path.join(src_dir, set_id, f"{set_id}_1K-JPG")
        color = Image.open(f"{base}_Color.jpg").resize((SIZE, SIZE), Image.LANCZOS)
        normal = (
            Image.open(f"{base}_NormalGL.jpg")
            .convert("RGB")
            .resize((SIZE, SIZE), Image.LANCZOS)
        )
        rough = (
            Image.open(f"{base}_Roughness.jpg")
            .convert("L")
            .resize((SIZE, SIZE), Image.LANCZOS)
        )

        lin = normalise_albedo(color)
        Image.fromarray(
            (linear_to_srgb(lin) * 255.0 + 0.5).astype(np.uint8), "RGB"
        ).save(os.path.join(out_dir, f"{kind}_c.webp"), quality=88, method=6)
        normal.save(os.path.join(out_dir, f"{kind}_n.webp"), quality=92, method=6)
        rough.save(os.path.join(out_dir, f"{kind}_r.webp"), quality=90, method=6)
        report[kind] = lin.reshape(-1, 3).mean(axis=0)
    return report


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = (
        sys.argv[2]
        if len(sys.argv) > 2
        else os.path.join(here, "..", "apps", "web", "public", "maps", "textures")
    )
    for kind, mean in process(sys.argv[1], out).items():
        print(f"{kind:11s} linear mean {np.round(mean, 4)}")
