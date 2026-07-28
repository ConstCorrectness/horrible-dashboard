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
 * Output is plain typed arrays rather than a `BufferGeometry`, so this module
 * never imports three: the panel lazy-loads three and uploads these. It also
 * makes the whole thing unit-testable with no WebGL context.
 */
import { CHF, FHF, World } from './world';

export interface MeshData {
  positions: Float32Array;
  normals: Float32Array;
  colors: Float32Array;
  /** Triangle count, for the HUD. */
  triangles: number;
}

/**
 * A stable, muted colour per texture id.
 *
 * Real AssaultCube textures are resolved through map config files this slice does
 * not read yet, so surfaces are tinted by texture id instead. That is not purely
 * a placeholder: giving distinct ids distinct hues is what makes walls, floors
 * and trim legible as architecture rather than a uniform grey soup.
 */
function texColor(tex: number, shade: number, out: [number, number, number]): void {
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

/** Face shading, so surfaces read apart without real lighting. */
const SHADE_FLOOR = 1.0;
const SHADE_CEIL = 0.55;
const SHADE_WALL_X = 0.8;
const SHADE_WALL_Y = 0.68;

class MeshBuilder {
  positions: number[] = [];
  normals: number[] = [];
  colors: number[] = [];
  private rgb: [number, number, number] = [0, 0, 0];

  /**
   * A quad as two triangles. Corners must be listed counter-clockwise as seen
   * from the side the normal points at, or the face is back-face culled and the
   * surface silently vanishes.
   */
  quad(
    ax: number,
    ay: number,
    az: number,
    bx: number,
    by: number,
    bz: number,
    cx: number,
    cy: number,
    cz: number,
    dx: number,
    dy: number,
    dz: number,
    nx: number,
    ny: number,
    nz: number,
    tex: number,
    shade: number,
  ): void {
    this.positions.push(ax, ay, az, bx, by, bz, cx, cy, cz);
    this.positions.push(ax, ay, az, cx, cy, cz, dx, dy, dz);
    for (let i = 0; i < 6; i++) this.normals.push(nx, ny, nz);
    texColor(tex, shade, this.rgb);
    const [r, g, b] = this.rgb;
    for (let i = 0; i < 6; i++) this.colors.push(r, g, b);
  }

  finish(): MeshData {
    return {
      positions: new Float32Array(this.positions),
      normals: new Float32Array(this.normals),
      colors: new Float32Array(this.colors),
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
        x,
        f00,
        y,
        x,
        f01,
        y + 1,
        x + 1,
        f11,
        y + 1,
        x + 1,
        f10,
        y,
        0,
        1,
        0,
        world.ftex[i],
        t === FHF ? SHADE_FLOOR * 0.95 : SHADE_FLOOR,
      );

      // Ceiling: normal down, so the opposite winding.
      b.quad(
        x,
        c00,
        y,
        x + 1,
        c10,
        y,
        x + 1,
        c11,
        y + 1,
        x,
        c01,
        y + 1,
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
    quadVertical(b, vx0, vy0, myF0, myC0, vx1, vy1, myF1, myC1, nrmX, nrmZ, tex, shade);
    return;
  }

  const nb = world.index(nx, ny);

  // Step up: the neighbour's floor sits above ours.
  if (world.floor[nb] > world.floor[world.index(x, y)]) {
    const top0 = world.cornerFloor(nx, ny, vx0, vy0);
    const top1 = world.cornerFloor(nx, ny, vx1, vy1);
    quadVertical(b, vx0, vy0, myF0, top0, vx1, vy1, myF1, top1, nrmX, nrmZ, world.wtex[nb], shade);
  }

  // Overhang: the neighbour's ceiling sits below ours.
  if (world.ceil[nb] < world.ceil[world.index(x, y)]) {
    const bot0 = world.cornerCeil(nx, ny, vx0, vy0);
    const bot1 = world.cornerCeil(nx, ny, vx1, vy1);
    quadVertical(b, vx0, vy0, bot0, myC0, vx1, vy1, bot1, myC1, nrmX, nrmZ, world.utex[nb], shade);
  }
}

/** A vertical quad across two edge vertices, each with its own bottom and top. */
function quadVertical(
  b: MeshBuilder,
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
  b.quad(
    vx0,
    bottom0,
    vy0,
    vx1,
    bottom1,
    vy1,
    vx1,
    top1,
    vy1,
    vx0,
    top0,
    vy0,
    nrmX,
    0,
    nrmZ,
    tex,
    shade,
  );
}
