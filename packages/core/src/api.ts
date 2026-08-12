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

// Mirror of the backend's capture policy (instrument.py): everything is truncated.
const MAX_BODY_CHARS = 2048;

/**
 * Paths whose bodies never reach the client I/O ring buffer — the mirror of
 * `_REDACT_BODY_PREFIXES` in instrument.py, and the same reasoning: these carry a
 * plaintext password up and a session token back, so capturing them would make the
 * observability panel the leak. The event is still recorded without its bodies.
 *
 * Keyed on path prefix rather than an argument at the call site so a new auth
 * route is covered by existing, not by remembering to opt out.
 */
const REDACT_BODY_PREFIXES = ['/games/auth/local'];

function redactsBody(path: string): boolean {
  return REDACT_BODY_PREFIXES.some((prefix) => path.startsWith(prefix));
}

function safeBody(body: string | null | undefined): string | null {
  if (!body) return null;
  return body.length > MAX_BODY_CHARS ? `${body.slice(0, MAX_BODY_CHARS)}… [truncated]` : body;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET';
  const start = performance.now();
  const requestBody = typeof init?.body === 'string' ? init.body : null;
  const requestBytes = requestBody?.length ?? null;
  const redacted = redactsBody(path);
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
      request_body: redacted ? null : safeBody(requestBody),
      response_body: redacted ? null : safeBody(text),
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

export function apiPost<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  });
}

export function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' });
}

/**
 * POST and consume a newline-delimited JSON stream, one object per callback.
 *
 * The backend's shape for anything with a progress bar — model pulls, llama.cpp
 * installs and GGUF downloads — because the transfer belongs to the request that
 * asked for it: navigating away cancels it, rather than leaving a `/ws` broadcast
 * running for nobody. Deliberately outside `request()`: that helper reads the whole
 * body before returning, which for a stream means the progress arrives all at once
 * at the end.
 *
 * `signal` aborts the read; the last partial line is dropped, since a truncated
 * line is not a JSON object.
 */
export async function streamNdjson(
  path: string,
  body: unknown,
  onLine: (obj: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(apiUrl(`${BASE}${path}`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  });
  if (!res.ok || !res.body)
    throw new ApiError(`API POST ${path} failed: ${res.status}`, res.status);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.trim()) onLine(JSON.parse(line) as Record<string, unknown>);
    }
  }
}
