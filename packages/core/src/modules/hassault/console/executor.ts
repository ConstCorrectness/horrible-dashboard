/**
 * Client command executor for hAssault Developer Console.
 *
 * Handles client-side CVar assignments, command chaining, history navigation,
 * custom keybinds/aliases, and REST/WebSocket dispatch to backend Python runtime.
 */

import { consoleRegistry } from './registry';
import type { ConsoleExecResult } from './types';

const HISTORY_STORAGE_KEY = 'hassault.console.history';
const BINDS_STORAGE_KEY = 'hassault.console.binds';
const ALIASES_STORAGE_KEY = 'hassault.console.aliases';
const MAX_HISTORY = 100;

export class ConsoleExecutor {
  history: string[] = [];
  historyIndex = -1;
  aliases = new Map<string, string>();
  binds = new Map<string, string>();

  constructor() {
    this.loadPersistedData();
  }

  private loadPersistedData(): void {
    try {
      const rawHist = localStorage.getItem(HISTORY_STORAGE_KEY);
      if (rawHist) this.history = JSON.parse(rawHist);
    } catch {
      // Unreadable or corrupt storage: start with an empty history. Console
      // recall is a convenience, and refusing to open the console because last
      // session's history will not parse would be the worse failure.
    }

    try {
      const rawBinds = localStorage.getItem(BINDS_STORAGE_KEY);
      if (rawBinds) {
        const obj = JSON.parse(rawBinds);
        for (const [k, v] of Object.entries(obj)) this.binds.set(k, String(v));
      }
    } catch {
      // Same: lost binds are re-bindable, an unopenable console is not.
    }

    try {
      const rawAliases = localStorage.getItem(ALIASES_STORAGE_KEY);
      if (rawAliases) {
        const obj = JSON.parse(rawAliases);
        for (const [k, v] of Object.entries(obj)) this.aliases.set(k, String(v));
      }
    } catch {
      // Same again for aliases.
    }
  }

  private persistHistory(): void {
    try {
      localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(this.history.slice(-MAX_HISTORY)));
    } catch {
      // Quota exceeded, or storage denied (private mode, embedded webview). The
      // in-memory history still works for this session; only persistence is lost.
    }
  }

  private persistBinds(): void {
    try {
      const obj = Object.fromEntries(this.binds.entries());
      localStorage.setItem(BINDS_STORAGE_KEY, JSON.stringify(obj));
    } catch {
      // As above — the bind is live, it just will not survive a reload.
    }
  }

  private persistAliases(): void {
    try {
      const obj = Object.fromEntries(this.aliases.entries());
      localStorage.setItem(ALIASES_STORAGE_KEY, JSON.stringify(obj));
    } catch {
      // As above.
    }
  }

  recordHistory(cmd: string): void {
    const trimmed = cmd.trim();
    if (!trimmed) return;
    if (this.history[this.history.length - 1] !== trimmed) {
      this.history.push(trimmed);
      this.persistHistory();
    }
    this.historyIndex = this.history.length;
  }

  historyPrev(): string | null {
    if (this.history.length === 0) return null;
    if (this.historyIndex > 0) {
      this.historyIndex -= 1;
      return this.history[this.historyIndex];
    }
    return this.history[0];
  }

  historyNext(): string | null {
    if (this.history.length === 0) return null;
    if (this.historyIndex < this.history.length - 1) {
      this.historyIndex += 1;
      return this.history[this.historyIndex];
    }
    this.historyIndex = this.history.length;
    return '';
  }

  bind(key: string, command: string): void {
    this.binds.set(key.toLowerCase(), command);
    this.persistBinds();
  }

  unbind(key: string): void {
    this.binds.delete(key.toLowerCase());
    this.persistBinds();
  }

  alias(name: string, command: string): void {
    this.aliases.set(name.toLowerCase(), command);
    this.persistAliases();
  }

  async execute(
    rawCommand: string,
    context?: { room?: string; player?: string },
  ): Promise<ConsoleExecResult> {
    const line = rawCommand.trim();
    if (!line) {
      return { ok: true, command: '', output: [], affected_cvars: {} };
    }

    this.recordHistory(line);

    // Expand alias if first token matches
    const firstToken = line.split(/\s+/)[0]?.toLowerCase();
    let expandedLine = line;
    if (firstToken && this.aliases.has(firstToken)) {
      expandedLine = line.replace(new RegExp(`^${firstToken}`, 'i'), this.aliases.get(firstToken)!);
    }

    // Handle Client-only special commands (bind, unbind, alias, clear)
    const tokens = expandedLine.split(/\s+/);
    const cmd = tokens[0].toLowerCase();

    if (cmd === 'bind' && tokens.length >= 3) {
      const key = tokens[1];
      const boundCmd = tokens.slice(2).join(' ').replace(/^["']|["']$/g, '');
      this.bind(key, boundCmd);
      return {
        ok: true,
        command: line,
        output: [`[bind] Bound '${key}' to "${boundCmd}"`],
        affected_cvars: {},
      };
    }

    if (cmd === 'unbind' && tokens.length >= 2) {
      const key = tokens[1];
      this.unbind(key);
      return {
        ok: true,
        command: line,
        output: [`[unbind] Unbound key '${key}'`],
        affected_cvars: {},
      };
    }

    if (cmd === 'alias' && tokens.length >= 3) {
      const aliasName = tokens[1];
      const aliasTarget = tokens.slice(2).join(' ').replace(/^["']|["']$/g, '');
      this.alias(aliasName, aliasTarget);
      return {
        ok: true,
        command: line,
        output: [`[alias] Created alias '${aliasName}' = "${aliasTarget}"`],
        affected_cvars: {},
      };
    }

    // Check if it's a client CVar assignment or query
    const targetCvar = consoleRegistry.cvars.get(tokens[0]);
    if (targetCvar && targetCvar.flags.includes('client') && !tokens[0].includes('(')) {
      if (tokens.length === 1) {
        return {
          ok: true,
          command: line,
          output: [
            `"${targetCvar.name}" is "${targetCvar.current_value}" (default "${targetCvar.default_value}") - ${targetCvar.description}`,
          ],
          affected_cvars: {},
          result_data: targetCvar.current_value,
        };
      }
      if (tokens.length === 2 || (tokens.length === 3 && tokens[1] === '=')) {
        const valStr = tokens.length === 3 ? tokens[2] : tokens[1];
        const ok = consoleRegistry.set(targetCvar.name, valStr);
        if (ok) {
          // `set` coerces in place, so the definition already holds the new value —
          // reading it back through `get` only reintroduced an `undefined` that
          // cannot happen on this branch.
          const newVal = targetCvar.current_value;
          return {
            ok: true,
            command: line,
            output: [`${targetCvar.name} = ${newVal}`],
            affected_cvars: { [targetCvar.name]: newVal },
            result_data: newVal,
          };
        }
      }
    }

    // Dispatch to Backend REST /api/hassault/console/exec
    try {
      const res = await fetch('/api/hassault/console/exec', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          command: expandedLine,
          room_id: context?.room || null,
          player_id: context?.player || null,
        }),
      });

      if (!res.ok) {
        const errText = await res.text();
        return {
          ok: false,
          command: line,
          output: [],
          error: `Server HTTP ${res.status}: ${errText}`,
          affected_cvars: {},
        };
      }

      const data = (await res.json()) as ConsoleExecResult;

      // Sync any affected CVars locally
      if (data.affected_cvars) {
        for (const [k, v] of Object.entries(data.affected_cvars)) {
          consoleRegistry.set(k, v);
        }
      }

      return data;
    } catch (err) {
      return {
        ok: false,
        command: line,
        output: [],
        error: `Network Error: ${err instanceof Error ? err.message : String(err)}`,
        affected_cvars: {},
      };
    }
  }
}

export const consoleExecutor = new ConsoleExecutor();
