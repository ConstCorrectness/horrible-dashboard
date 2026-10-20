# Photographed map surfaces

Colour, normal and roughness maps for the modelled maps' surfaces, read by the browser
client (fetched from `/maps/textures/`) and the native client (embedded with
`include_bytes!` in `apps/native-fps/src/textures3d.rs`).

All of them come from [ambientCG](https://ambientcg.com), released under
**CC0 1.0** (public domain dedication): free to use, change and ship, no
attribution required. They are credited here anyway.

| Kind (`glb-surfaces.json`) | ambientCG set       | Used for                         |
| -------------------------- | ------------------- | -------------------------------- |
| `sandstone`                | Ground093C          | ochre and dark sandstone walls   |
| `limewash`                 | Plaster002          | pale sandstone walls             |
| `paving`                   | PavingStones150     | limestone paving, plazas         |
| `dunesand`                 | Ground054           | sand banks                       |
| `cedar`                    | Wood092             | cedar beams and doors, palm bark |
| `souk_crate`               | Planks037A          | crates                           |
| `rust_iron`                | Metal053C           | rusted iron                      |
| `burlap`                   | Fabric081C          | awnings, canopies, sacks         |
| `glaze`                    | Tiles139            | glazed tile                      |
| `cast_concrete`            | Concrete031         | Nuke's concrete walls            |
| `slab_concrete`            | Concrete044A        | Nuke's ground and floors         |
| `corrugated`               | CorrugatedSteel007A | Nuke's shipping containers       |
| `painted_metal`            | PaintedMetal004     | Nuke's steel, pipes and drums    |
| `tread_plate`              | DiamondPlate003     | Nuke's catwalks and grating      |
| `yard_asphalt`             | Asphalt033          | Bank's street, Assault's yard    |
| `carpet_tile`              | Carpet016           | Office and Bank carpet           |
| `ceramic_tile`             | Tiles141            | server floor, Assault's offices  |
| `polished_marble`          | Marble012           | Office's floor, Bank's marble    |
| `red_brick`                | Bricks085           | Bank's and Assault's brick       |
| `wall_plaster`             | PaintedPlaster017   | Office, Bank, Assault walls      |

Each set is the 1K JPG download, cut down by `tools/hassault_process_textures.py`
to three 512 px WebP files per kind: `<kind>_c.webp` (albedo, normalised so a
surface keeps the colour the map authored), `<kind>_n.webp` (OpenGL-convention
normal) and `<kind>_r.webp` (roughness). To change one, download the new set,
edit `KINDS` in that script, and run it again (naming kinds after the output
directory processes only those, so the other sets need not be downloaded).

They are matched only by a map's own infixed material names (`dust2_`, `mirage_`,
`inferno_`, `nuke_`, `office_`, `bank_`, `assault_`), so no other map changes. See "The modelled maps" in `docs/modules/hassault.mdx`.
