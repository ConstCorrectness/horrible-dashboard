/**
 * MyST text → MyST mdast, the same AST Jupyter Book 2 builds from, so a Scrive block
 * and a JB block are one vocabulary.
 *
 * `myst-parser`'s raw parse (no transforms) is what this returns. It keeps what both
 * the renderer and a printer need: a directive carries its `name`, `args`,
 * `options`, its raw body as `value`, *and* the parsed body as `children` (an
 * admonition, a code-cell block…).
 *
 * Frontmatter is **not** handed to the parser — `splitFrontmatter` takes it off
 * first. The parser would turn it into a `code` node, and its line numbers would then
 * count the frontmatter, which every caller here would have to subtract back out.
 */
import yaml from 'js-yaml';
import { mystParse } from 'myst-parser';

export interface Position {
  start: { line: number; column?: number };
  end: { line: number; column?: number };
}

/** A MyST mdast node. Loose on purpose: the spec's own types lag the parser. */
export interface MystNode {
  type: string;
  children?: MystNode[];
  value?: string;
  position?: Position;
  [key: string]: unknown;
}

export interface MystRoot extends MystNode {
  type: 'root';
  children: MystNode[];
}

/** Frontmatter delimited by `---` lines at the very top, and the rest. */
export interface Frontmatter {
  /** The frontmatter block verbatim, delimiters and trailing newline included; `''`
   * when there is none. */
  raw: string;
  /** Everything after it, verbatim. `raw + body` is the original text. */
  body: string;
  /** How many lines `raw` spans — what a body line number is offset by. */
  lines: number;
}

const FRONTMATTER = /^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/;

export function splitFrontmatter(text: string): Frontmatter {
  const match = FRONTMATTER.exec(text);
  if (!match) return { raw: '', body: text, lines: 0 };
  const raw = match[0];
  const lines = raw.endsWith('\n') ? raw.split('\n').length - 1 : raw.split('\n').length;
  return { raw, body: text.slice(raw.length), lines };
}

/**
 * The frontmatter's data, or `{}`. JSON schema rather than YAML's default: dates stay
 * the strings the author typed (no timezone-shifted `Date`), and nothing in the file
 * can construct anything but plain data. Malformed YAML reads as empty, as on the
 * backend.
 */
export function readFrontmatter(raw: string): Record<string, unknown> {
  const inner = raw.replace(/^---[ \t]*\r?\n/, '').replace(/\r?\n---[ \t]*(?:\r?\n)?$/, '');
  try {
    const data = yaml.load(inner, { schema: yaml.JSON_SCHEMA });
    return data && typeof data === 'object' && !Array.isArray(data)
      ? (data as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

/**
 * Parse a MyST body (frontmatter already removed). Never throws: a parser exception
 * on some pathological input becomes a single raw node, so a broken page still
 * shows its text rather than an error where the document should be.
 */
export function parseMyst(body: string): MystRoot {
  try {
    return mystParse(body) as unknown as MystRoot;
  } catch (err) {
    return {
      type: 'root',
      children: [{ type: 'scriveParseError', value: body, message: String(err) }],
    };
  }
}
