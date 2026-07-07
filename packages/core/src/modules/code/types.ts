/** Shapes returned by the backend code index (`/api/code/*`). Named `Code*` to
 * avoid shadowing the global `Symbol`/`Range`. Mirrors backend/modules/code/models.py. */

export interface CodePosition {
  line: number; // 1-based
  column: number; // 1-based
}
export interface CodeRange {
  start: CodePosition;
  end: CodePosition;
}
export interface CodeSymbol {
  name: string;
  kind: string; // function | method | class | interface | type | enum
  range: CodeRange;
  container?: string | null;
}
export interface DocumentSymbols {
  path: string;
  language: string | null;
  symbols: CodeSymbol[];
}
export interface SymbolHit extends CodeSymbol {
  path: string;
}
export interface FindResult {
  query: string;
  hits: SymbolHit[];
}

/** A semantic-search hit: a definition plus its cosine score (fields nullable — read
 * from stored metadata). */
export interface SemanticHit {
  name: string | null;
  kind: string | null;
  container?: string | null;
  path: string | null;
  range: CodeRange | null;
  score: number;
}

export interface SemanticSearchResult {
  query: string;
  building: boolean; // a reindex is in flight; results may be empty/partial
  results: SemanticHit[];
}
