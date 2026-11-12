/**
 * The native client this server offers for download, as `/download/info` says it.
 *
 * Asked rather than assumed: a deploy made from a machine that could not build a
 * Windows executable carries none, and a link written into the page would then be
 * a button that downloads a 404. See `backend/games_server/downloads.py`.
 */

export interface NativeBuild {
  /** Server-relative path of the file, always under `/download/`. */
  url: string;
  /** The client's version, or `''` when the server did not say. */
  version: string;
  sizeBytes: number;
}

/**
 * The Windows build out of a `/download/info` answer, or null when there is
 * none to offer.
 *
 * Strict on the two fields the page acts on. The size is shown to someone
 * deciding whether to start a download, so a missing one is not a zero. The url
 * becomes an `href`, so it has to be this server's own download path — an answer
 * naming anywhere else is not a link this page should put in front of a player.
 */
export function parseDownloadInfo(info: unknown): NativeBuild | null {
  if (!info || typeof info !== 'object') return null;
  const windows = (info as { windows?: unknown }).windows;
  if (!windows || typeof windows !== 'object') return null;
  const { url, version, sizeBytes } = windows as Record<string, unknown>;
  if (typeof url !== 'string' || !/^\/download\/[\w.-]+$/.test(url)) return null;
  if (typeof sizeBytes !== 'number' || !Number.isFinite(sizeBytes) || sizeBytes <= 0) return null;
  return { url, version: typeof version === 'string' ? version : '', sizeBytes };
}
