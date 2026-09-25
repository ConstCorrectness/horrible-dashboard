/**
 * Which workbench is on screen. Kept out of `IdeWorkbench.tsx` so the manifest and
 * the agent tools can ask without importing the workbench — which pulls in
 * CodeMirror, and would put it back on the boot path.
 */
let liveHostId: string | null = null;

/** The workbench instance currently rendering, or null when none is open. */
export function workbenchInstanceId(): string | null {
  return liveHostId;
}

/** Set by the mounted workbench; `null` clears it only if `hostId` still owns it. */
export function setWorkbenchInstanceId(hostId: string | null, owner?: string): void {
  if (hostId === null && owner !== undefined && liveHostId !== owner) return;
  liveHostId = hostId;
}
