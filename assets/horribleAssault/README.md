# HorribleAssault raw art

**Everything in this directory except this file is git-ignored.** It is the
_input_ to a build step, not an input to the app — see "Why none of this is
committed" below. A fresh clone builds and runs both clients with this directory
empty.

If you are not regenerating the character, you do not need any of it.

---

## What the build actually consumes

Only two entries here are read by anything:

| Path             | Size   | What it is                                                                                                              |
| ---------------- | ------ | ----------------------------------------------------------------------------------------------------------------------- |
| `green swat.fbx` | 35 MB  | The **rigged** character — Mixamo auto-rigger output, 42 `mixamorig:` bones, skin clusters, all five map kinds embedded |
| `animations/`    | 6.4 MB | The 23 skinless Mixamo clip FBXs, one per clip                                                                          |

Together they produce `apps/web/public/hassault-operator.glb`, which **is**
committed, and which both clients load — the browser fetches it, and the native
client compiles it in with `include_bytes!`.

> [!WARNING]
> Do not reach for `t-pose-male-green-swat/source/green swat.zip`. That is the
> original Sketchfab download and contains `green swat.obj`, which has **no
> skeleton and no skin weights**. Binding a `mixamorig:` clip to it fails
> silently: everything loads and the character stands in a permanent T-pose. The
> build script's bone-agreement check turns that into a hard error, which is the
> only reason it is not a mystery. Same for the female variant.
>
> The usage example in `scripts/build_hassault_character.mjs`'s header names
> `t-pose-male-green-swat/source/rigged.fbx`, which does not exist. The real
> rigged source is `green swat.fbx`, at the top level of this directory.

## Regenerating the character

```bash
pnpm build:hassault-character -- \
  --character "assets/horribleAssault/green swat.fbx" \
  --animations assets/horribleAssault/animations \
  --out apps/web/public/hassault-operator.glb \
  --manifest packages/core/src/modules/hassault/models/clips.json
```

Defaults that the committed GLB was built with, recorded because they are not
obvious and the manifest only pins two of them (`clips.json` carries
`targetHeight` and `rootMotion`):

| Flag               | Value   | Why                                                                                                                                                              |
| ------------------ | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--height`         | `5.2`   | The canonical standing height, mirroring `DEFAULT_HITBOX.standingHeight`. A mesh taller than the cylinder it represents has its head somewhere it cannot be shot |
| `--root-motion`    | `strip` | The server owns position; a clip that walks the hips across the floor fights it and the body skates                                                              |
| `--texture-size`   | `1024`  | 33.9 MB of PNG becomes 2.9 MB of webp at this size                                                                                                               |
| `--texture-format` | `webp`  | See the note on `EXT_texture_webp` below                                                                                                                         |

After rebuilding, verify rather than assume — the failures here are silent:

- `apps/web/operator-check.html` (`/operator-check.html` under `pnpm dev`) —
  reports clip count, height, and **bones moved per mesh**. The per-mesh column
  is the one that matters: the failure mode is a _partial_ T-pose where the shirt
  animates and the body does not.
- `cargo run --manifest-path apps/native-fps/Cargo.toml --example operator_preview`
  — the same five clips, rendered offscreen by the native client.
- `cargo test --manifest-path apps/native-fps/Cargo.toml --lib character::` —
  asserts 34 bones, every joint index in range, and a 5.2-cube bind pose.

> [!NOTE]
> The exporter writes webp and declares `EXT_texture_webp` as a **required**
> extension, which stock glTF parsers must refuse. The native client folds the
> extension away in memory before parsing (`character.rs`, `normalise_glb`). If
> you rebuild with `--texture-format png`, that path simply stops being needed;
> it does not break.

## Mixamo export settings

The rig and all 23 clips come from [Mixamo](https://www.mixamo.com), which needs
an Adobe login. There is no scriptable URL — this is a manual step, which is a
large part of why the files are ignored rather than fetched on demand.

For the **character** (`green swat.fbx`): upload the mesh, run the auto-rigger,
download as FBX **with skin**.

For each **clip** in `animations/`: download as FBX **without skin** — Mixamo
names its bones identically across every export, so a skinless clip is pure
skeleton keyframes that bind to any Mixamo-rigged mesh by bone name. Leave
**"In Place" checked** where the option exists; the build strips residual hip
translation anyway and reports the drift each clip carried, but starting from
In Place gives it less to do.

The filename becomes the clip name, lowercased with spaces to underscores —
`Standard Walk.fbx` → `standard_walk`. Those names are the key both clients look
clips up by (`models/clips.ts`, `clips.rs`), and a rename on either side is a
clip that silently resolves to nothing.

## Provenance and licences

**Fill this in.** It is the one thing here that cannot be re-derived, and it is
the reason this file is committed while the art is not: the bytes are
downloadable again, the knowledge of _what they are and what you may do with
them_ is not.

The module's standing rule is that other people's content is **supported, never
bundled** — which is why AssaultCube's maps are read from the user's own install
and why the in-game weapon props are procedural boxes rather than these models.
Anything below that is used in a shipped artifact needs its licence checked
against that rule first.

| Path                              | Size   | Source                                      | Licence | Used by                                            |
| --------------------------------- | ------ | ------------------------------------------- | ------- | -------------------------------------------------- |
| `green swat.fbx`                  | 35 MB  | Mixamo auto-rig of the Sketchfab mesh below | ?       | **The operator GLB**                               |
| `animations/` (23 FBX)            | 6.4 MB | Mixamo                                      | ?       | **The operator GLB**                               |
| `t-pose-male-green-swat/`         | 63 MB  | Sketchfab                                   | ?       | Nothing — kept for its full-resolution `textures/` |
| `t-pose-female-green-swat/`       | 63 MB  | Sketchfab                                   | ?       | Nothing                                            |
| `buildings/`                      | 145 MB | ?                                           | ?       | Nothing                                            |
| `m4-carbine-rifle/`               | 81 MB  | ?                                           | ?       | Nothing                                            |
| `appartement.zip`                 | 71 MB  | ?                                           | ?       | Nothing                                            |
| `carbine-m4a1/`                   | 62 MB  | ?                                           | ?       | Nothing                                            |
| `remington-870-express-tactical/` | 39 MB  | ?                                           | ?       | Nothing                                            |
| `svu-a-sniper-rifle/`             | 25 MB  | ?                                           | ?       | Nothing                                            |
| `beretta-92/`                     | 13 MB  | ?                                           | ?       | Nothing                                            |
| `Ch50_nonPBR.fbx`                 | 121 MB | Mixamo character                            | ?       | Nothing                                            |
| `Yaku J Ignite.fbx`               | 20 MB  | Mixamo character                            | ?       | Nothing                                            |
| `Ch18_nonPBR.fbx`                 | 14 MB  | Mixamo character                            | ?       | Nothing                                            |
| `cat.zip`                         | 1.1 MB | ?                                           | ?       | Nothing                                            |

About 600 MB of the 746 MB here is used by nothing. The weapon models are
already documented as _"third-party models with unverified licences"_
(`docs/modules/hassault.mdx`), which is precisely why the weapon props in both
clients are procedural. Treat that column's `?` as a blocker for any use, not as
a formality.

## Why none of this is committed

- **Size.** 746 MB, against a `.git` already around 618 MB. Git keeps every
  version forever, so committing once and deleting later costs it in every clone
  from then on.
- **It is not needed to build or run.** The output is committed; the input is
  not. Only regenerating the character needs these files.
- **It cannot be fetched on demand either.** The rig and every clip sit behind an
  Adobe login with per-export options; re-hosting them from our own storage would
  be redistributing art whose licences are the `?` column above.

If this directory ever does need to travel with the repo, the answer is Git LFS
or a fetch script against storage you control — not a plain commit.
