# HorribleAssault map captures

Stills from the game's own renderer — real textures, fog and light, no HUD,
viewmodel or minimap — at 1920×938. Six framings per map:
`<map>-<target>_<height>.webp`.

- **target** `0`/`1` are the A/B bomb sites (where the map has them), `2` is
  the midpoint between the two teams' spawns. Facility and Junk Flea have no
  sites, so their three targets are the map centre and two corners.
- **height** `lo` is just above eye level from a spawn; `hi` is 12 units up,
  tilted down.

The ones in use on the game site are copied (downscaled to 1600px) into
`apps/assault-web/src/assets/maps/<id>.webp`:

| Map         | Shot   |
| ----------- | ------ |
| hd_dust2    | `2_lo` |
| hd_assault  | `1_lo` |
| hd_mirage   | `2_lo` |
| hd_inferno  | `1_lo` |
| hd_nuke     | `0_lo` |
| hd_office   | `1_lo` |
| hd_bank     | `0_hi` |
| hd_facility | `0_lo` |
| hd_junkflea | `1_lo` |

Office's `hi` shots are above the ceiling and unusable.

## Re-shooting

Blender renders of the shipped `hd_*.glb` don't work for this: the GLBs carry
flat colours only, and the textures and atmosphere are added at runtime. Capture
from the game instead, in a **foreground** Chrome tab (a background tab pauses
`requestAnimationFrame`, so nothing renders):

1. Host the map on the assault-web dev server and deploy.
2. `window.__VM` is the viewmodel; walk `__VM.pivot.parent` up to the
   `PerspectiveCamera`.
3. Wrap `__VM.update(dt, f)`: call the original with `{ ...f, visible: false }`
   to hide the gun, then set `camera.position` / `camera.lookAt`. The game
   positions the camera before calling it, so the override wins each frame.
   Three.js coordinates are `(x, height, mapJson.y)`; eye height is 4.5.
4. Hide every DOM element except the largest `<canvas>` (the minimap is a
   second canvas).
5. Read the frame with `canvas.toDataURL()` inside a `queueMicrotask` queued
   from the wrapped update — that runs after the render and before the drawing
   buffer is cleared.
