/**
 * TypeScript types for the hAssault Developer Console, CVars, ConCommands, and Macros.
 */

export type CVarType = 'boolean' | 'number' | 'string' | 'enum';
export type CVarFlag = 'cheat' | 'server' | 'client' | 'replicated' | 'archived' | 'readonly';

export interface CVarDefinition {
  name: string;
  namespace: string;
  type: CVarType;
  default_value: any;
  current_value: any;
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
  default?: any;
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
  affected_cvars: Record<string, any>;
  result_data?: any;
}

export interface AutocompleteItem {
  name: string;
  kind: 'cvar' | 'command' | 'macro';
  type?: string;
  currentValue?: any;
  defaultValue?: any;
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
