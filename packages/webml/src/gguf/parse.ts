/**
 * Read a GGUF file's header — metadata and tensor directory — from any byte source
 * that can serve ranges: an HTTP Range endpoint, an OPFS file, bytes in memory.
 *
 * A TypeScript port of `backend/modules/interpretability/gguf.py`, with the same
 * bounds and the same refusals; the two readers must agree on what a file holds.
 * Like that one it never reads past `dataOffset`: the header is a few MB (most of
 * it the vocabulary) in front of gigabytes of weights, which is what lets the
 * playground say whether a model can run here *before* anything large downloads.
 *
 * Format (ggml-org/ggml, docs/gguf.md), little-endian:
 *     magic "GGUF" | version u32 | tensor_count u64 | kv_count u64
 *     kv_count × ( key:string, type:u32, value )
 *     tensor_count × ( name:string, n_dims:u32, dims:u64[n_dims], type:u32, offset:u64 )
 *     padding to general.alignment
 *     tensor data
 */
import { ggmlTypeName, tensorBytes } from './ggml';

/** Something that can serve byte ranges of one file. */
export interface ByteSource {
  /** The file's total size, when the source knows it. */
  readonly size: number | null;
  /** Up to `length` bytes from `offset`; fewer only at the end of the file. */
  read(offset: number, length: number): Promise<Uint8Array<ArrayBuffer>>;
}

export class GgufError extends Error {
  override name = 'GgufError';
}

/** A metadata value. Numeric arrays are typed arrays: a 152k-entry vocabulary's scores
 * as a `number[]` would be several MB of boxed doubles. */
export type GgufValue =
  | number
  | bigint
  | boolean
  | string
  | GgufValue[]
  | Uint8Array
  | Int8Array
  | Uint16Array
  | Int16Array
  | Uint32Array
  | Int32Array
  | Float32Array
  | Float64Array;

export interface GgufTensor {
  name: string;
  /** ggml order: `shape[0]` is the innermost (row) dimension. */
  shape: number[];
  type: number;
  typeName: string;
  /** Relative to `dataOffset`. */
  offset: number;
  elements: number;
  /** Null for a type this reader has no size for (see `ggml.ts`). */
  bytes: number | null;
}

export interface GgufHeader {
  version: number;
  alignment: number;
  /** Absolute file offset where tensor data starts. */
  dataOffset: number;
  /** The file's size, when the source knew it. */
  fileSize: number | null;
  metadata: Record<string, GgufValue>;
  tensors: GgufTensor[];
}

// A malformed or truncated file must fail fast rather than make us allocate from a
// length field read out of it. Same bounds as gguf.py: far above any real model,
// far below anything that would exhaust memory.
const MAX_TENSORS = 100_000;
const MAX_KV = 100_000;
const MAX_STRING = 64 * 1024 * 1024;
const MAX_DIMS = 8;
const MAX_ARRAY = 8_000_000;

// Metadata value types.
const T_UINT8 = 0;
const T_INT8 = 1;
const T_UINT16 = 2;
const T_INT16 = 3;
const T_UINT32 = 4;
const T_INT32 = 5;
const T_FLOAT32 = 6;
const T_BOOL = 7;
const T_STRING = 8;
const T_ARRAY = 9;
const T_UINT64 = 10;
const T_INT64 = 11;
const T_FLOAT64 = 12;

const SCALAR_SIZE: Record<number, number> = {
  [T_UINT8]: 1,
  [T_INT8]: 1,
  [T_UINT16]: 2,
  [T_INT16]: 2,
  [T_UINT32]: 4,
  [T_INT32]: 4,
  [T_FLOAT32]: 4,
  [T_BOOL]: 1,
  [T_UINT64]: 8,
  [T_INT64]: 8,
  [T_FLOAT64]: 8,
};

type TypedArrayCtor = {
  new (buffer: ArrayBuffer): Exclude<GgufValue, number | bigint | boolean | string | GgufValue[]>;
};

const TYPED_ARRAYS: Record<number, TypedArrayCtor> = {
  [T_UINT8]: Uint8Array,
  [T_INT8]: Int8Array,
  [T_UINT16]: Uint16Array,
  [T_INT16]: Int16Array,
  [T_UINT32]: Uint32Array,
  [T_INT32]: Int32Array,
  [T_FLOAT32]: Float32Array,
  [T_FLOAT64]: Float64Array,
};

/** The first fetch; enough for most headers that are not mostly vocabulary. */
const FIRST_CHUNK = 2 * 1024 * 1024;
/** Later fetches double up to this, so a 10 MB header takes a handful of requests. */
const MAX_CHUNK = 16 * 1024 * 1024;

/**
 * A forward-only cursor over a ByteSource, fetching ahead in growing chunks and
 * dropping what has been consumed.
 */
class Cursor {
  private buf = new Uint8Array(0);
  /** File offset of `buf[0]`. */
  private start = 0;
  /** Read position, as a file offset. */
  pos = 0;
  private chunk = FIRST_CHUNK;
  private eof = false;
  // ignoreBOM keeps a string's leading U+FEFF: Gemma's vocabulary has `\ufeff#`
  // beside `#`, and a decoder that drops it makes the two one token.
  private readonly utf8 = new TextDecoder('utf-8', { fatal: false, ignoreBOM: true });

  constructor(private readonly source: ByteSource) {}

  /** Make `n` bytes from `pos` available; returns a view of them. */
  async take(n: number): Promise<{ view: DataView; bytes: Uint8Array }> {
    const end = this.start + this.buf.length;
    if (this.pos + n > end) await this.fill(this.pos + n - end);
    const at = this.pos - this.start;
    const bytes = this.buf.subarray(at, at + n);
    this.pos += n;
    return { view: new DataView(bytes.buffer, bytes.byteOffset, n), bytes };
  }

  private async fill(missing: number): Promise<void> {
    if (this.eof) throw new GgufError(`Truncated: wanted ${missing} more bytes at ${this.pos}`);
    const end = this.start + this.buf.length;
    const want = Math.max(missing, this.chunk);
    this.chunk = Math.min(this.chunk * 2, MAX_CHUNK);
    const got = await this.source.read(end, want);
    if (got.length < want) this.eof = true;
    if (got.length < missing) {
      throw new GgufError(`Truncated: wanted ${missing} more bytes at ${end}, got ${got.length}`);
    }
    const keepFrom = this.pos - this.start;
    const next = new Uint8Array(this.buf.length - keepFrom + got.length);
    next.set(this.buf.subarray(keepFrom));
    next.set(got, this.buf.length - keepFrom);
    this.buf = next;
    this.start = this.pos;
  }

  async u32(): Promise<number> {
    return (await this.take(4)).view.getUint32(0, true);
  }

  /** A u64 that is a length, count or offset: it must fit a JS number exactly. */
  async u64(what: string): Promise<number> {
    const v = (await this.take(8)).view.getBigUint64(0, true);
    if (v > BigInt(Number.MAX_SAFE_INTEGER)) throw new GgufError(`Implausible ${what} ${v}`);
    return Number(v);
  }

  async string(): Promise<string> {
    const length = await this.u64('string length');
    if (length > MAX_STRING) throw new GgufError(`Implausible string length ${length}`);
    // Replacement characters rather than a throw, as gguf.py: one bad byte in one
    // tokenizer token must not cost the whole tensor directory after it.
    return this.utf8.decode((await this.take(length)).bytes);
  }

  async value(type: number): Promise<GgufValue> {
    const size = SCALAR_SIZE[type];
    if (size !== undefined) {
      const { view } = await this.take(size);
      return scalar(view, 0, type);
    }
    if (type === T_STRING) return this.string();
    if (type === T_ARRAY) {
      const itemType = await this.u32();
      const count = await this.u64('array length');
      if (count > MAX_ARRAY) throw new GgufError(`Implausible array length ${count}`);
      const Typed = TYPED_ARRAYS[itemType];
      if (Typed) {
        const { bytes } = await this.take(count * SCALAR_SIZE[itemType]);
        // A copy: aligned for the typed array, and not pinning the read buffer.
        return new Typed(bytes.slice().buffer);
      }
      const out: GgufValue[] = new Array(count);
      for (let i = 0; i < count; i++) out[i] = await this.value(itemType);
      return out;
    }
    throw new GgufError(`Unknown metadata value type ${type}`);
  }
}

function scalar(view: DataView, at: number, type: number): number | bigint | boolean {
  switch (type) {
    case T_UINT8:
      return view.getUint8(at);
    case T_INT8:
      return view.getInt8(at);
    case T_UINT16:
      return view.getUint16(at, true);
    case T_INT16:
      return view.getInt16(at, true);
    case T_UINT32:
      return view.getUint32(at, true);
    case T_INT32:
      return view.getInt32(at, true);
    case T_FLOAT32:
      return view.getFloat32(at, true);
    case T_BOOL:
      return view.getUint8(at) !== 0;
    case T_FLOAT64:
      return view.getFloat64(at, true);
    case T_UINT64: {
      const v = view.getBigUint64(at, true);
      return v <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(v) : v;
    }
    default: {
      const v = view.getBigInt64(at, true);
      return v <= BigInt(Number.MAX_SAFE_INTEGER) && v >= BigInt(Number.MIN_SAFE_INTEGER)
        ? Number(v)
        : v;
    }
  }
}

/** Parse a GGUF header. Throws `GgufError` for anything that is not a GGUF we can read. */
export async function readGgufHeader(source: ByteSource): Promise<GgufHeader> {
  const c = new Cursor(source);
  const magic = (await c.take(4)).bytes;
  if (String.fromCharCode(...magic) !== 'GGUF') {
    throw new GgufError(`Not a GGUF file (magic ${JSON.stringify(String.fromCharCode(...magic))})`);
  }
  const version = await c.u32();
  if (version < 2 || version > 3) {
    // v1 used u32 lengths and is long dead; a big-endian file reads as a huge version.
    throw new GgufError(`Unsupported GGUF version ${version}`);
  }
  const tensorCount = await c.u64('tensor count');
  const kvCount = await c.u64('metadata count');
  if (tensorCount > MAX_TENSORS || kvCount > MAX_KV) {
    throw new GgufError(`Implausible header (${tensorCount} tensors, ${kvCount} metadata keys)`);
  }

  const metadata: Record<string, GgufValue> = {};
  for (let i = 0; i < kvCount; i++) {
    const key = await c.string();
    metadata[key] = await c.value(await c.u32());
  }

  const tensors: GgufTensor[] = [];
  for (let i = 0; i < tensorCount; i++) {
    const name = await c.string();
    const nDims = await c.u32();
    if (nDims > MAX_DIMS)
      throw new GgufError(`Tensor ${JSON.stringify(name)} claims ${nDims} dimensions`);
    const shape: number[] = [];
    let elements = 1;
    for (let d = 0; d < nDims; d++) {
      const dim = await c.u64('dimension');
      shape.push(dim);
      elements *= dim;
    }
    if (!Number.isSafeInteger(elements)) {
      throw new GgufError(`Tensor ${JSON.stringify(name)} has an implausible shape`);
    }
    const type = await c.u32();
    const offset = await c.u64('tensor offset');
    tensors.push({
      name,
      shape,
      type,
      typeName: ggmlTypeName(type),
      offset,
      elements,
      bytes: tensorBytes(type, elements),
    });
  }

  const declared = metadata['general.alignment'];
  const alignment =
    typeof declared === 'number' && Number.isInteger(declared) && declared > 0 ? declared : 32;
  const here = c.pos;
  return {
    version,
    alignment,
    dataOffset: here + ((alignment - (here % alignment)) % alignment),
    fileSize: source.size,
    metadata,
    tensors,
  };
}

/**
 * What is wrong with a header's tensor layout, as sentences; empty when it is sound.
 *
 * Every tensor must have a known size, start on an alignment boundary, not overlap
 * the next, and lie inside the file. A file that fails this would upload the wrong
 * bytes as weights — and a truncated download fails it, which is the case worth
 * catching before a single buffer is created.
 */
export function layoutProblems(header: GgufHeader): string[] {
  const problems: string[] = [];
  const sorted = [...header.tensors].sort((a, b) => a.offset - b.offset);
  let end = 0;
  let previous = '';
  for (const t of sorted) {
    if (t.bytes === null) {
      problems.push(`${t.name}: no size for type ${t.typeName} with ${t.elements} elements`);
      continue;
    }
    if (t.offset % header.alignment) {
      problems.push(`${t.name}: offset ${t.offset} is not ${header.alignment}-aligned`);
    }
    if (t.offset < end) problems.push(`${t.name}: overlaps ${previous}`);
    end = Math.max(end, t.offset + t.bytes);
    previous = t.name;
  }
  if (header.fileSize !== null && header.dataOffset + end > header.fileSize) {
    problems.push(
      `tensor data ends at ${header.dataOffset + end}, past the end of the file (${header.fileSize})`,
    );
  }
  return problems;
}

/** Bytes of tensor data the header describes (tensors with an unknown size count as 0). */
export function tensorDataBytes(header: GgufHeader): number {
  let total = 0;
  for (const t of header.tensors) total += t.bytes ?? 0;
  return total;
}
