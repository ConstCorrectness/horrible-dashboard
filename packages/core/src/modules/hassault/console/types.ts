/**
 * TypeScript types for the hAssault Developer Console, CVars, ConCommands, and Macros.
 */

export type CVarType = 'boolean' | 'number' | 'string' | 'enum';

/**
 * What a CVar can hold. Not `any`: `_coerce_cvar_value` in
 * `backend/modules/hassault/console.py` returns exactly bool, int/float or str for
 * the four `CVarType`s (an enum is one of its string members), so this is the whole
 * set rather than a convenient shrug. The backend models these as `Any` because
 * Pydantic has no reason to narrow them; the client does, because every consumer
 * has to decide how to render one.
 */
export type CVarValue = boolean | number | string;
export type CVarFlag = 'cheat' | 'server' | 'client' | 'replicated' | 'archived' | 'readonly';

export interface CVarDefinition {
  name: string;
  namespace: string;
  type: CVarType;
  default_value: CVarValue;
  current_value: CVarValue;
  min_value?: number | null;
  max_value?: number | null;
  enum_values?: string[] | null;
  description: string;
  flags: CVarFlag[];
  python_attr?: string;
}

export interface ConCommandParameter {
  name: string;
  type: string;
  default?: CVarValue | null;
  description: string;
  required: boolean;
  enum_values?: string[] | null;
}

export interface ConCommandDefinition {
  name: string;
  namespace: string;
  description: string;
  signature: string;
  parameters: ConCommandParameter[];
  flags: string[];
  example: string;
}

export interface MacroRecord {
  name: string;
  description: string;
  code: string;
  author: string;
  builtin: boolean;
  created_at: number;
  updated_at: number;
}

export type ConsoleLogLevel = 'input' | 'output' | 'error' | 'warning' | 'info' | 'system';

export interface ConsoleLogEntry {
  id: string;
  timestamp: number;
  level: ConsoleLogLevel;
  text: string;
  source?: 'client' | 'server' | 'macro';
}

export interface ConsoleExecResult {
  ok: boolean;
  command: string;
  output: string[];
  error?: string | null;
  affected_cvars: Record<string, CVarValue>;
  /** Whatever the command chose to return — genuinely arbitrary per command, so
   *  `unknown` rather than `any`: a caller must narrow it before use. */
  result_data?: unknown;
}

export interface AutocompleteItem {
  name: string;
  kind: 'cvar' | 'command' | 'macro';
  type?: string;
  currentValue?: CVarValue;
  defaultValue?: CVarValue;
  signature?: string;
  description: string;
  flags?: string[];
}

export interface NetGraphStats {
  fps: number;
  ping: number;
  inKbps: number;
  outKbps: number;
  interpDelayMs: number;
  jitterMs: number;
  lossPct: number;
  chokePct: number;
  tickRate: number;
}
