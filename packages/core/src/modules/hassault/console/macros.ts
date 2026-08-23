/**
 * Macro management for hAssault Developer Console.
 */

import type { MacroRecord } from './types';

export async function fetchMacros(): Promise<MacroRecord[]> {
  try {
    const res = await fetch('/api/hassault/console/macros');
    if (!res.ok) return [];
    return (await res.json()) as MacroRecord[];
  } catch {
    return [];
  }
}

export async function saveMacro(
  name: string,
  code: string,
  description = '',
): Promise<MacroRecord | null> {
  try {
    const res = await fetch('/api/hassault/console/macros', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, code, description }),
    });
    if (!res.ok) return null;
    return (await res.json()) as MacroRecord;
  } catch {
    return null;
  }
}

export async function deleteMacro(name: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/hassault/console/macros/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
    return res.ok;
  } catch {
    return false;
  }
}
