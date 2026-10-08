/**
 * A minimal GGUF writer for building malformed and edge-case files in tests. The
 * well-formed reference file comes from llama.cpp's own writer instead
 * (scripts/gen_webml_gguf_fixture.py); this one exists to write what that one won't.
 */

export class Bytes {
  private parts: Uint8Array[] = [];
  length = 0;

  raw(bytes: Uint8Array | number[]): this {
    const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    this.parts.push(b);
    this.length += b.length;
    return this;
  }

  private view(size: number, fill: (v: DataView) => void): this {
    const b = new Uint8Array(size);
    fill(new DataView(b.buffer));
    return this.raw(b);
  }

  u8 = (n: number) => this.view(1, (v) => v.setUint8(0, n));
  u32 = (n: number) => this.view(4, (v) => v.setUint32(0, n, true));
  u64 = (n: number | bigint) => this.view(8, (v) => v.setBigUint64(0, BigInt(n), true));
  f32 = (n: number) => this.view(4, (v) => v.setFloat32(0, n, true));

  /** A GGUF string: u64 length + bytes (UTF-8 for a JS string, verbatim for bytes). */
  str(s: string | Uint8Array): this {
    const b = typeof s === 'string' ? new TextEncoder().encode(s) : s;
    return this.u64(b.length).raw(b);
  }

  done(): Uint8Array {
    const out = new Uint8Array(this.length);
    let at = 0;
    for (const p of this.parts) {
      out.set(p, at);
      at += p.length;
    }
    return out;
  }
}

export interface TensorSpec {
  name: string;
  shape: number[];
  type: number;
  offset: number;
}

/** Header fields up to (not including) the metadata. */
export function preamble(tensors: number, kvs: number, version = 3): Bytes {
  return new Bytes().raw([0x47, 0x47, 0x55, 0x46]).u32(version).u64(tensors).u64(kvs);
}

export function tensorEntry(b: Bytes, t: TensorSpec): Bytes {
  b.str(t.name).u32(t.shape.length);
  for (const d of t.shape) b.u64(d);
  return b.u32(t.type).u64(t.offset);
}

/**
 * A file: metadata written by `kv`, then the tensor directory, then zero data up to
 * `dataBytes` past the aligned data offset.
 */
export function gguf(opts: {
  kvs?: number;
  kv?: (b: Bytes) => void;
  tensors?: TensorSpec[];
  alignment?: number;
  dataBytes?: number;
}): Uint8Array {
  const tensors = opts.tensors ?? [];
  const b = preamble(tensors.length, opts.kvs ?? 0);
  opts.kv?.(b);
  for (const t of tensors) tensorEntry(b, t);
  const align = opts.alignment ?? 32;
  b.raw(new Uint8Array((align - (b.length % align)) % align));
  b.raw(new Uint8Array(opts.dataBytes ?? 0));
  return b.done();
}
