import { registry } from '@horrible/core';

/**
 * The shell's slot for module status readouts (`ModuleManifest.shellIndicators`).
 *
 * Rendered in both chrome surfaces — the status bar's tray and, on the native
 * shell, the titlebar — so the canonical indicator ("your screen is being
 * broadcast") is on screen whichever one the user is looking at.
 *
 * Each indicator renders null when it has nothing to say; this wrapper adds no
 * box of its own when none of them render anything, since `display: contents`
 * leaves no gap behind.
 */
export function ShellIndicators() {
  const indicators = registry.shellIndicators;
  if (indicators.length === 0) return null;
  return (
    <div className="shell-indicators">
      {indicators.map(({ id, component: Indicator }) => (
        <Indicator key={id} />
      ))}
    </div>
  );
}
