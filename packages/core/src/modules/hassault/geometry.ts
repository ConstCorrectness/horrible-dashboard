/**
 * Turning the cube grid into triangles.
 *
 * The rule is the one the Cube engine uses: an open cell contributes a floor and
 * a ceiling, and a **wall appears wherever an open cell meets something taller**
 * — a solid neighbour, a neighbour with a higher floor, or one with a lower
 * ceiling. Emitting a wall from the open side only means every surface is
 * produced exactly once and always faces the space you can stand in, so the mesh
 * needs no back faces.
 *
 * Two things beyond triangles are baked in here, both because this is the only
 * place that can see the grid:
 *
 * - **Ambient occlusion, per vertex.** Nothing else makes untextured blocky
 *   geometry read as architecture: without it a corner where three walls meet is
 *   the same flat colour as the middle of a corridor, and the eye has nothing to
 *   resolve depth against. It is computed from the columns around each corner and
 *   folded into the vertex colour, so it costs exactly nothing at draw time.
 * - **UVs, in cube units.** A surface detail texture needs somewhere to land, and
 *   world-space-planar UVs mean it tiles continuously across a floor rather than
 *   restarting inside every cell.
 *
 * Output is plain typed arrays rather than a `BufferGeometry`, so this module
 * never imports three: the panel lazy-loads three and uploads these. It also
 * makes the whole thing unit-testable with no WebGL context.
 */
import { CHF, FHF, World } from './world';

export interface MeshData {
  positions: Float32Array;
  normals: Float32Array;
  /** Texture tint and baked occlusion, multiplied together. */
  colors: Float32Array;
  /** Planar, in cube units, so a detail texture tiles across cells. */
  uvs: Float32Array;
  /** Triangle count, for the HUD. */
  triangles: number;
}

/**
 * How dark a fully occluded corner goes, as a fraction of its unoccluded colour.
 *
 * Tuned by what it has to achieve rather than by taste: the darkest crease must
 * still be distinguishable from black, because these surfaces have no texture to
 * carry detail once the light is gone.
 */
const AO_STRENGTH = 0.46;

/** Height slack when asking whether a column blocks a point. */
const AO_EPS = 0.05;

/**
 * The standard voxel corner-AO curve.
 *
 * Two adjacent occluders meeting at a corner are the *worst* case — they close
 * the corner completely — so they count as three rather than two. Skipping that
 * rule is what produces the "corner is lighter than the edges leading into it"
 * artefact.
 */
function cornerAO(side1: boolean, side2: boolean, corner: boolean): number {
  const occluders = side1 && side2 ? 3 : (side1 ? 1 : 0) + (side2 ? 1 : 0) + (corner ? 1 : 0);
  return 1 - (occluders / 3) * AO_STRENGTH;
}

/**
 * Whether the column at `(x, y)` fills the space at height `h`.
 *
 * Out of bounds counts as filled for the same reason `isSolid` does: the engine
 * guarantees a solid border, and treating the outside as open would put a bright
 * rim around every map.
 */
function blocksAt(world: World, x: number, y: number, h: number): boolean {
  if (!world.inBounds(x, y)) return true;
  if (world.isSolid(x, y)) return true;
  return world.floorAt(x, y) > h + AO_EPS || world.ceilAt(x, y) < h - AO_EPS;
}

/**
 * A stable, muted colour per texture id.
 *
 * Real AssaultCube textures are resolved through map config files this slice does
 * not read yet, so surfaces are tinted by texture id instead. That is not purely
 * a placeholder: giving distinct ids distinct hues is what makes walls, floors
 * and trim legible as architecture rather than a uniform grey soup.
 *
 * Exported for `surface-conformance.test.ts` and for the generator that writes
 * its fixture. `geometry.rs` has the same function, and until that fixture
 * existed nothing checked that the two agreed — a drift here is the same map
 * rendered in different colours depending on which client you launched.
 */
export function texColor(tex: number, shade: number, out: [number, number, number]): void {
  // Golden-ratio hue stepping puts adjacent ids far apart in hue.
  const hue = (tex * 0.618033988749895) % 1;
  const sat = 0.22;
  const light = Math.min(0.95, 0.55 * shade);
  const f = (n: number) => {
    const k = (n + hue * 12) % 12;
    const a = sat * Math.min(light, 1 - light);
    return light - a * Math.max(-1, Math.min(Math.min(k - 3, 9 - k), 1));
  };
  out[0] = f(0);
  out[1] = f(8);
  out[2] = f(4);
}

/**
 * Face shading, so surfaces read apart without real lighting.
 *
 * Exported with the tint, and for the same reason: a shade is one of the two
 * inputs to `texColor`, so pinning the function while leaving the constants
 * private would pin agreement on colours neither client ever actually draws.
 */
export const SHADE_FLOOR = 1.0;
export const SHADE_CEIL = 0.55;
export const SHADE_WALL_X = 0.8;
export const SHADE_WALL_Y = 0.68;

class MeshBuilder {
  positions: number[] = [];
  normals: number[] = [];
  colors: number[] = [];
  uvs: number[] = [];
  private rgb: [number, number, number] = [0, 0, 0];

  /**
   * A quad as two triangles. Corners must be listed counter-clockwise as seen
   * from the side the normal points at, or the face is back-face culled and the
   * surface silently vanishes.
   *
   * `ao` and `uv` are per corner, in the same order as the positions. The
   * diagonal is chosen by the occlusion rather than fixed: a quad whose two
   * *opposite* corners are the dark pair, split the wrong way, shades as a
   * visible crease across the middle of an otherwise flat surface — the classic
   * voxel-AO seam.
   */
  quad(
    corners: readonly [
      readonly [number, number, number],
      readonly [number, number, number],
      readonly [number, number, number],
      readonly [number, number, number],
    ],
    uv: readonly [
      readonly [number, number],
      readonly [number, number],
      readonly [number, number],
      readonly [number, number],
    ],
    ao: readonly [number, number, number, number],
    nx: number,
    ny: number,
    nz: number,
    tex: number,
    shade: number,
  ): void {
    texColor(tex, shade, this.rgb);
    const [r, g, b] = this.rgb;
    // a-c or b-d: pick the split that keeps the two brightest corners together.
    const order = ao[0] + ao[2] > ao[1] + ao[3] ? [0, 1, 2, 0, 2, 3] : [1, 2, 3, 1, 3, 0];
    for (const i of order) {
      const [px, py, pz] = corners[i];
      this.positions.push(px, py, pz);
      this.normals.push(nx, ny, nz);
      this.uvs.push(uv[i][0], uv[i][1]);
      const k = ao[i];
      this.colors.push(r * k, g * k, b * k);
    }
  }

  finish(): MeshData {
    return {
      positions: new Float32Array(this.positions),
      normals: new Float32Array(this.normals),
      colors: new Float32Array(this.colors),
      uvs: new Float32Array(this.uvs),
      triangles: this.positions.length / 9,
    };
  }
}

/**
 * Build the world mesh. Solid cells contribute nothing, which implicitly skips
 * the two-cube solid border the engine guarantees.
 */
export function buildWorldMesh(world: World): MeshData {
  const b = new MeshBuilder();
  const { ssize } = world;

  for (let y = 0; y < ssize; y++) {
    for (let x = 0; x < ssize; x++) {
      if (world.isSolid(x, y)) continue;
      const i = world.index(x, y);
      const t = world.type[i];

      // Corner heights. For a flat cell all four come out equal, so flat cells
      // and heightfields go through exactly one path.
      const f00 = world.cornerFloor(x, y, x, y);
      const f10 = world.cornerFloor(x, y, x + 1, y);
      const f11 = world.cornerFloor(x, y, x + 1, y + 1);
      const f01 = world.cornerFloor(x, y, x, y + 1);
      const c00 = world.cornerCeil(x, y, x, y);
      const c10 = world.cornerCeil(x, y, x + 1, y);
      const c11 = world.cornerCeil(x, y, x + 1, y + 1);
      const c01 = world.cornerCeil(x, y, x, y + 1);

      // Floor: normal up, wound CCW seen from above.
      b.quad(
        [
          [x, f00, y],
          [x, f01, y + 1],
          [x + 1, f11, y + 1],
          [x + 1, f10, y],
        ],
        [
          [x, y],
          [x, y + 1],
          [x + 1, y + 1],
          [x + 1, y],
        ],
        [
          horizontalAO(world, x, y, -1, -1, f00),
          horizontalAO(world, x, y, -1, 1, f01),
          horizontalAO(world, x, y, 1, 1, f11),
          horizontalAO(world, x, y, 1, -1, f10),
        ],
        0,
        1,
        0,
        world.ftex[i],
        t === FHF ? SHADE_FLOOR * 0.95 : SHADE_FLOOR,
      );

      // Ceiling: normal down, so the opposite winding.
      b.quad(
        [
          [x, c00, y],
          [x + 1, c10, y],
          [x + 1, c11, y + 1],
          [x, c01, y + 1],
        ],
        [
          [x, y],
          [x + 1, y],
          [x + 1, y + 1],
          [x, y + 1],
        ],
        [
          horizontalAO(world, x, y, -1, -1, c00),
          horizontalAO(world, x, y, 1, -1, c10),
          horizontalAO(world, x, y, 1, 1, c11),
          horizontalAO(world, x, y, -1, 1, c01),
        ],
        0,
        -1,
        0,
        world.ctex[i],
        t === CHF ? SHADE_CEIL * 0.95 : SHADE_CEIL,
      );

      emitWall(b, world, x, y, -1, 0);
      emitWall(b, world, x, y, 1, 0);
      emitWall(b, world, x, y, 0, -1);
      emitWall(b, world, x, y, 0, 1);
    }
  }
  return b.finish();
}

/**
 * Occlusion at one corner of a floor or ceiling in cell `(x, y)`.
 *
 * `(dx, dy)` is which corner, as a direction from the cell's middle. The three
 * columns that can shadow it are the two orthogonal neighbours and the diagonal
 * between them — the cell's own column is excluded by construction, since a
 * surface cannot occlude itself.
 */
function horizontalAO(
  world: World,
  x: number,
  y: number,
  dx: number,
  dy: number,
  h: number,
): number {
  return cornerAO(
    blocksAt(world, x + dx, y, h),
    blocksAt(world, x, y + dy, h),
    blocksAt(world, x + dx, y + dy, h),
  );
}

/**
 * Occlusion at one corner of a wall, whose face plane is vertical.
 *
 * The in-plane neighbours are the column beside it along the wall (`lateral`)
 * and the space directly above or below the corner:
 *
 * - A **bottom** corner always meets the floor it stands on, which is why the
 *   crease along the base of every wall exists at all.
 * - A **top** corner meets a ceiling only when the wall actually reaches one, so
 *   a low step gets no dark line along its top edge while a full-height wall in a
 *   closed corridor does.
 *
 * `open` is the cell the wall faces into: occlusion is a property of the space in
 * front of a surface, so asking about the *solid* side would shade every wall by
 * what is behind it.
 */
function wallAO(
  world: World,
  open: { x: number; y: number },
  lx: number,
  ly: number,
  h: number,
  bottom: boolean,
): number {
  const lateral = blocksAt(world, open.x + lx, open.y + ly, h);
  const vertical = bottom
    ? h <= world.floorAt(open.x, open.y) + AO_EPS
    : h >= world.ceilAt(open.x, open.y) - AO_EPS;
  // The diagonal: the lateral column one step further along the wall's vertical
  // axis, which is what closes the corner where a wall meets a floor *and* a
  // neighbouring wall at once.
  const diagonal = blocksAt(world, open.x + lx, open.y + ly, h + (bottom ? -1 : 1));
  return cornerAO(lateral, vertical, diagonal);
}

/**
 * The wall between open cell `(x, y)` and its neighbour in direction `(dx, dy)`.
 *
 * Three cases: a solid neighbour walls off this cell's full height; an open
 * neighbour with a higher floor gives a step up (textured `wtex`); one with a
 * lower ceiling gives an overhang (textured `utex` — the *upper* wall texture,
 * and using `wtex` there is a classic source of subtly wrong walls).
 *
 * Both sides read the same two shared vertices, so heights meet exactly and no
 * seam opens even across heightfields.
 */
function emitWall(
  b: MeshBuilder,
  world: World,
  x: number,
  y: number,
  dx: number,
  dy: number,
): void {
  const nx = x + dx;
  const ny = y + dy;

  // The shared edge as two grid vertices, ordered so the quad winds CCW when
  // seen from inside this cell.
  let vx0: number, vy0: number, vx1: number, vy1: number;
  if (dx === -1) {
    vx0 = x;
    vy0 = y + 1;
    vx1 = x;
    vy1 = y;
  } else if (dx === 1) {
    vx0 = x + 1;
    vy0 = y;
    vx1 = x + 1;
    vy1 = y + 1;
  } else if (dy === -1) {
    vx0 = x;
    vy0 = y;
    vx1 = x + 1;
    vy1 = y;
  } else {
    vx0 = x + 1;
    vy0 = y + 1;
    vx1 = x;
    vy1 = y + 1;
  }

  // The normal points from the neighbour back into this cell — the open side.
  const nrmX = -dx;
  const nrmZ = -dy;
  const shade = dx !== 0 ? SHADE_WALL_X : SHADE_WALL_Y;

  const myF0 = world.cornerFloor(x, y, vx0, vy0);
  const myF1 = world.cornerFloor(x, y, vx1, vy1);
  const myC0 = world.cornerCeil(x, y, vx0, vy0);
  const myC1 = world.cornerCeil(x, y, vx1, vy1);

  if (world.isSolid(nx, ny)) {
    // A solid cube stores only `wtex`; out of bounds falls back to ours.
    const tex = world.inBounds(nx, ny)
      ? world.wtex[world.index(nx, ny)]
      : world.wtex[world.index(x, y)];
    quadVertical(
      b,
      world,
      x,
      y,
      vx0,
      vy0,
      myF0,
      myC0,
      vx1,
      vy1,
      myF1,
      myC1,
      nrmX,
      nrmZ,
      tex,
      shade,
    );
    return;
  }

  const nb = world.index(nx, ny);

  // Step up: the neighbour's floor sits above ours.
  if (world.floor[nb] > world.floor[world.index(x, y)]) {
    const top0 = world.cornerFloor(nx, ny, vx0, vy0);
    const top1 = world.cornerFloor(nx, ny, vx1, vy1);
    quadVertical(
      b,
      world,
      x,
      y,
      vx0,
      vy0,
      myF0,
      top0,
      vx1,
      vy1,
      myF1,
      top1,
      nrmX,
      nrmZ,
      world.wtex[nb],
      shade,
    );
  }

  // Overhang: the neighbour's ceiling sits below ours.
  if (world.ceil[nb] < world.ceil[world.index(x, y)]) {
    const bot0 = world.cornerCeil(nx, ny, vx0, vy0);
    const bot1 = world.cornerCeil(nx, ny, vx1, vy1);
    quadVertical(
      b,
      world,
      x,
      y,
      vx0,
      vy0,
      bot0,
      myC0,
      vx1,
      vy1,
      bot1,
      myC1,
      nrmX,
      nrmZ,
      world.utex[nb],
      shade,
    );
  }
}

/**
 * A vertical quad across two edge vertices, each with its own bottom and top.
 *
 * `(ox, oy)` is the open cell the face looks into, which the occlusion needs and
 * the geometry does not — a wall's shading belongs to the room in front of it.
 */
function quadVertical(
  b: MeshBuilder,
  world: World,
  ox: number,
  oy: number,
  vx0: number,
  vy0: number,
  bottom0: number,
  top0: number,
  vx1: number,
  vy1: number,
  bottom1: number,
  top1: number,
  nrmX: number,
  nrmZ: number,
  tex: number,
  shade: number,
): void {
  if (top0 <= bottom0 && top1 <= bottom1) return; // degenerate: nothing to draw

  // The lateral step from the open cell toward each edge vertex: the wall runs
  // along one axis, so one component is always zero.
  const open = { x: ox, y: oy };
  const l0x = nrmX !== 0 ? 0 : Math.sign(vx0 - (ox + 0.5));
  const l0y = nrmZ !== 0 ? 0 : Math.sign(vy0 - (oy + 0.5));
  const l1x = nrmX !== 0 ? 0 : Math.sign(vx1 - (ox + 0.5));
  const l1y = nrmZ !== 0 ? 0 : Math.sign(vy1 - (oy + 0.5));

  // U runs along the wall in cube units, V is height, so a detail texture keeps
  // one scale everywhere instead of stretching with a wall's size.
  const u0 = vx0 + vy0;
  const u1 = vx1 + vy1;

  b.quad(
    [
      [vx0, bottom0, vy0],
      [vx1, bottom1, vy1],
      [vx1, top1, vy1],
      [vx0, top0, vy0],
    ],
    [
      [u0, bottom0],
      [u1, bottom1],
      [u1, top1],
      [u0, top0],
    ],
    [
      wallAO(world, open, l0x, l0y, bottom0, true),
      wallAO(world, open, l1x, l1y, bottom1, true),
      wallAO(world, open, l1x, l1y, top1, false),
      wallAO(world, open, l0x, l0y, top0, false),
    ],
    nrmX,
    0,
    nrmZ,
    tex,
    shade,
  );
}
