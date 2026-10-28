import * as fs from 'node:fs';
import * as net from 'node:net';
import * as os from 'node:os';
import * as path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import {
  BUNDLED_RUNTIME_DIR,
  StderrTail,
  backendCommand,
  bindHost,
  bundledRuntimeIn,
  findRepoRoot,
  isHealthResponse,
  isOurBackend,
  parseEnvFile,
  pickPort,
  resolveRuntime,
  sanitizedPath,
} from '../src/backend';

const isWindows = process.platform === 'win32';
const scratchDirs: string[] = [];

function scratch(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hd-backend-test-'));
  scratchDirs.push(dir);
  return dir;
}

afterEach(() => {
  for (const dir of scratchDirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

/** Build a directory that looks like a finished bundled runtime. */
function layDownRuntime(resourceDir: string, withBackend: boolean, withPython: boolean): void {
  const root = path.join(resourceDir, BUNDLED_RUNTIME_DIR);
  fs.mkdirSync(root, { recursive: true });
  if (withBackend) {
    fs.mkdirSync(path.join(root, 'backend'), { recursive: true });
    fs.writeFileSync(path.join(root, 'backend', 'app.py'), '');
  }
  if (withPython) {
    const dir = isWindows ? path.join(root, 'python') : path.join(root, 'python', 'bin');
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, isWindows ? 'python.exe' : 'python3'), '');
  }
}

describe('runtime resolution', () => {
  it('resolves a complete bundled runtime', () => {
    const dir = scratch();
    layDownRuntime(dir, true, true);
    expect(bundledRuntimeIn(dir)?.root).toBe(path.join(dir, BUNDLED_RUNTIME_DIR));
  });

  // The half-shipped cases: an interrupted install or a bundler that dropped part of
  // a 50,000-file tree. "No backend found" points at the install; spawning it would
  // surface a ModuleNotFoundError and send the reader hunting through app code.
  it('does not resolve an incomplete runtime', () => {
    const empty = scratch();
    expect(bundledRuntimeIn(empty)).toBeNull();
    const noPython = scratch();
    layDownRuntime(noPython, true, false);
    expect(bundledRuntimeIn(noPython)).toBeNull();
    const noSource = scratch();
    layDownRuntime(noSource, false, true);
    expect(bundledRuntimeIn(noSource)).toBeNull();
  });

  it('prefers a checkout over a bundled runtime', () => {
    const bundle = scratch();
    layDownRuntime(bundle, true, true);
    const checkout = scratch();
    fs.writeFileSync(path.join(checkout, 'pyproject.toml'), '');
    fs.mkdirSync(path.join(checkout, 'backend', 'deep', 'er'), { recursive: true });
    const runtime = resolveRuntime([path.join(checkout, 'backend', 'deep', 'er')], bundle);
    expect(runtime).toEqual({ kind: 'checkout', root: checkout });
  });

  it('falls back to the bundle outside a checkout, and to nothing without one', () => {
    const bundle = scratch();
    layDownRuntime(bundle, true, true);
    const elsewhere = scratch();
    expect(resolveRuntime([elsewhere], bundle)?.kind).toBe('bundled');
    expect(resolveRuntime([elsewhere], null)).toBeNull();
  });

  it('finds this repository from inside it', () => {
    const root = findRepoRoot([__dirname]);
    expect(root && fs.existsSync(path.join(root, 'backend'))).toBeTruthy();
  });

  it('runs the bundled interpreter directly, with no uv', () => {
    const dir = scratch();
    layDownRuntime(dir, true, true);
    const cmd = backendCommand(bundledRuntimeIn(dir)!, '127.0.0.1', 8123);
    expect(cmd.usesUv).toBe(false);
    expect(cmd.args).toEqual([
      '-m',
      'uvicorn',
      'backend.app:app',
      '--host',
      '127.0.0.1',
      '--port',
      '8123',
    ]);
  });

  it('uses the venv python when synced, else `uv run`', () => {
    const checkout = scratch();
    expect(backendCommand({ kind: 'checkout', root: checkout }, '0.0.0.0', 1).command).toBe('uv');
    const venv = isWindows
      ? path.join(checkout, '.venv', 'Scripts', 'python.exe')
      : path.join(checkout, '.venv', 'bin', 'python');
    fs.mkdirSync(path.dirname(venv), { recursive: true });
    fs.writeFileSync(venv, '');
    expect(backendCommand({ kind: 'checkout', root: checkout }, '0.0.0.0', 1).command).toBe(venv);
  });
});

describe('environment', () => {
  it('parses .env lines, stripping matching quotes', () => {
    expect(
      parseEnvFile(
        '# comment\nA=1\n B = "two words" \nC=\'x\'\nnot a pair\nD="unbalanced\r\nE=a=b',
      ),
    ).toEqual([
      ['A', '1'],
      ['B', 'two words'],
      ['C', 'x'],
      ['D', '"unbalanced'],
      ['E', 'a=b'],
    ]);
  });

  it('binds loopback unless the launcher asks for more', () => {
    expect(bindHost({})).toBe('127.0.0.1');
    expect(bindHost({ HORRIBLE_DEV_HOST: '  ' })).toBe('127.0.0.1');
    expect(bindHost({ HORRIBLE_DEV_HOST: '0.0.0.0' })).toBe('0.0.0.0');
  });

  it('drops MinGW bin dirs from PATH', () => {
    expect(
      sanitizedPath(
        'C:\\Windows;C:\\msys64\\mingw64\\bin;C:\\Program Files\\Git\\mingw64\\bin;C:\\x',
        true,
      ),
    ).toBe('C:\\Windows;C:\\x');
    expect(sanitizedPath('/usr/bin:/opt/mingw64/bin:/bin', false)).toBe('/usr/bin:/bin');
    expect(sanitizedPath(undefined)).toBeUndefined();
  });
});

describe('our backend vs a stranger', () => {
  it('only a healthy response naming the app is ours', () => {
    expect(
      isHealthResponse(
        'HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{"status":"ok","app":"horrible-dashboard"}',
      ),
    ).toBe(true);
    // The case that shipped once: something else on 8000 answering everything with 500.
    expect(
      isHealthResponse('HTTP/1.0 500 Internal Server Error\r\n\r\nInternal Server Error'),
    ).toBe(false);
    expect(isHealthResponse('HTTP/1.1 200 OK\r\n\r\n{"status":"ok"}')).toBe(false);
    expect(
      isHealthResponse('HTTP/1.1 404 Not Found\r\n\r\nno route for "horrible-dashboard"'),
    ).toBe(false);
    expect(isHealthResponse('')).toBe(false);
  });

  /** Serve one canned response on an ephemeral loopback port. */
  function serveOnce(response: string): Promise<{ port: number; close: () => void }> {
    return new Promise((resolve) => {
      const server = net.createServer((socket) => {
        socket.once('data', () => socket.end(response));
      });
      server.listen(0, '127.0.0.1', () => {
        const port = (server.address() as net.AddressInfo).port;
        resolve({ port, close: () => server.close() });
      });
    });
  }

  it('tells them apart on the wire', async () => {
    const ours = await serveOnce(
      'HTTP/1.0 200 OK\r\n\r\n{"status":"ok","app":"horrible-dashboard"}',
    );
    const stranger = await serveOnce('HTTP/1.0 500 Internal Server Error\r\n\r\nnope');
    try {
      expect(await isOurBackend(ours.port)).toBe(true);
      expect(await isOurBackend(stranger.port)).toBe(false);
    } finally {
      ours.close();
      stranger.close();
    }
  });

  it('skips a held port for an ephemeral one', async () => {
    const held = await serveOnce('');
    try {
      const port = await pickPort('127.0.0.1', held.port);
      expect(port).not.toBe(held.port);
      expect(port).toBeGreaterThan(0);
    } finally {
      held.close();
    }
  });
});

describe('stderr tail', () => {
  // cp1252's em dash (0x97) is not UTF-8. The drain must keep reading through it —
  // a closed read end breaks every later stderr write in the backend.
  it('survives a line that is not UTF-8 and keeps a bounded tail', () => {
    const tail = new StderrTail();
    tail.push(Buffer.from('first\r\nlocale \x97 dash\nla', 'latin1'));
    tail.push('st\n');
    tail.push('no newline');
    tail.end();
    const lines = tail.text().split('\n');
    expect(lines[0]).toBe('first');
    expect(lines[1]!.startsWith('locale ')).toBe(true);
    expect(lines.slice(2)).toEqual(['last', 'no newline']);
    for (let i = 0; i < 200; i++) tail.push(`line ${i}\n`);
    expect(tail.text().split('\n')).toHaveLength(50);
  });
});
