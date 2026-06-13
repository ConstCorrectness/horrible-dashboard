/**
 * Backend API client. Both layouts reach the backend through the same relative
 * base: the web dev server proxies /api to localhost:8000, and the Tauri shell
 * loads the same frontend from that dev server (dev) or a configured origin.
 */
import { apiUrl } from './origin';
import { recordClientIo } from './telemetry';

const BASE = '/api';

/** Thrown for non-2xx responses; carries the status so callers can branch on it. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// Mirror of the backend's capture policy (instrument.py): bodies on sensitive
// routes are suppressed, everything else is truncated.
const SENSITIVE_PATH_PREFIXES = ['/clubhouse'];
const MAX_BODY_CHARS = 2048;

function safeBody(body: string | null | undefined, path: string): string | null {
  if (!body) return null;
  if (SENSITIVE_PATH_PREFIXES.some((p) => path.startsWith(p))) {
    return '[redacted — sensitive route]';
  }
  return body.length > MAX_BODY_CHARS ? `${body.slice(0, MAX_BODY_CHARS)}… [truncated]` : body;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET';
  const start = performance.now();
  const requestBody = typeof init?.body === 'string' ? init.body : null;
  const requestBytes = requestBody?.length ?? null;
  try {
    const res = await fetch(apiUrl(`${BASE}${path}`), init);
    const text = await res.text();
    recordClientIo({
      method,
      target: path,
      status: res.status,
      duration_ms: performance.now() - start,
      request_bytes: requestBytes,
      response_bytes: text.length,
      request_headers: init?.headers ? { ...(init.headers as Record<string, string>) } : null,
      response_headers: Object.fromEntries(res.headers.entries()),
      request_body: safeBody(requestBody, path),
      response_body: safeBody(text, path),
    });
    if (!res.ok) {
      // Surface the backend's `detail` (FastAPI HTTPException) instead of a bare
      // status, so widgets can show the real reason.
      let message = `${method} ${path} failed: ${res.status}`;
      try {
        const parsed = JSON.parse(text) as { detail?: unknown };
        if (typeof parsed.detail === 'string') message = parsed.detail;
      } catch {
        // non-JSON error body — keep the status message
      }
      throw new ApiError(message, res.status);
    }
    return JSON.parse(text) as T;
  } catch (err) {
    if (err instanceof TypeError) {
      // fetch rejected (network/CORS) — no response was recorded above.
      recordClientIo({
        method,
        target: path,
        status: null,
        duration_ms: performance.now() - start,
        request_bytes: requestBytes,
        error: String(err),
      });
    }
    throw err;
  }
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function apiPut<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' });
}
