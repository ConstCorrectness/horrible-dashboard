/**
 * Order module packages so dependencies register first. The TypeScript twin of
 * `backend/sdk/module_scan.py:load_order` — same edges, same tie-break, same
 * refusal to fail: a cycle is reported and loaded in input order, because a
 * running app with a logged cycle beats a blank one.
 *
 * Edges: `dependsOn`, and `requires` → whichever module `provides` that contract.
 */
import type { ModulePackageManifest } from '@horribledashboard/sdk';

type Orderable = Pick<ModulePackageManifest, 'id' | 'dependsOn' | 'provides' | 'requires'>;

export function loadOrder(manifests: Orderable[]): { order: string[]; problems: string[] } {
  const ids = manifests.map((m) => m.id);
  const present = new Set(ids);
  const providers = new Map<string, string>();
  for (const m of manifests) {
    for (const c of m.provides ?? []) if (!providers.has(c)) providers.set(c, m.id);
  }
  const problems: string[] = [];
  const deps = new Map<string, Set<string>>();
  for (const m of manifests) {
    const d = new Set<string>();
    for (const dep of Object.keys(m.dependsOn ?? {})) {
      if (present.has(dep)) d.add(dep);
      else problems.push(`${m.id} depends on missing module "${dep}"`);
    }
    for (const c of m.requires ?? []) {
      const p = providers.get(c);
      if (p && p !== m.id) d.add(p);
    }
    deps.set(m.id, d);
  }

  const order: string[] = [];
  const done = new Set<string>();
  let remaining = ids;
  while (remaining.length) {
    const ready = remaining.filter((i) => [...deps.get(i)!].every((d) => done.has(d)));
    if (!ready.length) {
      problems.push(`dependency cycle among: ${remaining.join(', ')}`);
      order.push(...remaining);
      break;
    }
    for (const i of ready) {
      order.push(i);
      done.add(i);
    }
    remaining = remaining.filter((i) => !done.has(i));
  }
  return { order, problems };
}
