/**
 * Backend API client. Both layouts reach the backend through the same relative
 * base: the web dev server proxies /api to localhost:8000, and the Tauri shell
 * loads the same frontend from that dev server (dev) or a configured origin.
 */
import { recordClientIo } from './telemetry';

const BASE = '/api';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET';
  const start = performance.now();
  const requestBytes = typeof init?.body === 'string' ? init.body.length : null;
  try {
    const res = await fetch(`${BASE}${path}`, init);
    const text = await res.text();
    recordClientIo({
      method,
      target: path,
      status: res.status,
      duration_ms: performance.now() - start,
      request_bytes: requestBytes,
      response_bytes: text.length,
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
      throw new Error(message);
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
