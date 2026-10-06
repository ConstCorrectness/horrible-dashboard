# Photographed map surfaces

Colour, normal and roughness maps for Dust II's surfaces, read by the browser
client (fetched from `/maps/textures/`) and the native client (embedded with
`include_bytes!` in `apps/native-fps/src/textures3d.rs`).

All of them come from [ambientCG](https://ambientcg.com), released under
**CC0 1.0** (public domain dedication): free to use, change and ship, no
attribution required. They are credited here anyway.

| Kind (`glb-surfaces.json`) | ambientCG set   | Used for                         |
| -------------------------- | --------------- | -------------------------------- |
| `sandstone`                | Ground093C      | ochre and dark sandstone walls   |
| `limewash`                 | Plaster002      | pale sandstone walls             |
| `paving`                   | PavingStones150 | limestone paving, plazas         |
| `dunesand`                 | Ground054       | sand banks                       |
| `cedar`                    | Wood092         | cedar beams and doors, palm bark |
| `souk_crate`               | Planks037A      | crates                           |
| `rust_iron`                | Metal053C       | rusted iron                      |
| `burlap`                   | Fabric081C      | awnings, canopies, sacks         |
| `glaze`                    | Tiles139        | glazed tile                      |

Each set is the 1K JPG download, cut down by `tools/hassault_process_textures.py`
to three 512 px WebP files per kind: `<kind>_c.webp` (albedo, normalised so a
surface keeps the colour the map authored), `<kind>_n.webp` (OpenGL-convention
normal) and `<kind>_r.webp` (roughness). To change one, download the new set,
edit `KINDS` in that script, and run it again.

They are matched only by Dust II's own `dust2_` material names, so no other map
changes. See "The modelled maps" in `docs/modules/hassault.mdx`.
