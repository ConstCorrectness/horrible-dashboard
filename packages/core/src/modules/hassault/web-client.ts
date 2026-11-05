/**
 * Where the browser client lives, for the launcher's **Open in browser**.
 *
 * The game server serves `apps/assault-web` at its own origin, so the page is the
 * server's address with the socket scheme swapped for the page one:
 * `wss://horrible-games.fly.dev` → `https://horrible-games.fly.dev/`. The node
 * already reports that address (`/games/status` → `server_url`, the
 * `games.serverUrl` setting resolved), so nothing here keeps a second copy of it.
 */

/** The browser client's URL for a game server address, or `null` when the
 * address is missing or not a server the page could be served from. */
export function webClientUrl(server: string): string | null {
  let url: URL;
  try {
    url = new URL(server.trim());
  } catch {
    return null;
  }
  const scheme = { 'wss:': 'https:', 'ws:': 'http:', 'https:': 'https:', 'http:': 'http:' }[
    url.protocol
  ];
  if (!scheme) return null;
  // The origin only: a path on the server address names the socket route, not
  // the page.
  return `${scheme}//${url.host}/`;
}
