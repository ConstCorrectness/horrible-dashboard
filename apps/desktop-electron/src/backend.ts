import { app } from 'electron';
import { spawn, type ChildProcess } from 'node:child_process';
import * as fs from 'node:fs';
import * as http from 'node:http';
import * as path from 'node:path';
import type { BackendStatus } from './types.js';

const DEFAULT_PORT = 8000;
const READY_TIMEOUT_MS = 90_000;
const POLL_INTERVAL_MS = 250;

export class BackendSupervisor {
  private status: BackendStatus = {
    state: 'starting',
    origin: null,
    error: null,
  };

  private child: ChildProcess | null = null;
  private shuttingDown = false;
  private rootDir: string;

  constructor(appPath: string) {
    // Resolve repo root: check if appPath or parent directories contain pyproject.toml
    this.rootDir = this.findRepoRoot(appPath);
  }

  private findRepoRoot(startDir: string): string {
    let current = startDir;
    for (let i = 0; i < 5; i++) {
      if (
        fs.existsSync(path.join(current, 'pyproject.toml')) &&
        fs.existsSync(path.join(current, 'backend', 'app.py'))
      ) {
        return current;
      }
      const parent = path.dirname(current);
      if (parent === current) break;
      current = parent;
    }
    return startDir;
  }

  public getStatus(): BackendStatus {
    return { ...this.status };
  }

  public async start(): Promise<void> {
    const host = process.env.HORRIBLE_DEV_HOST || '127.0.0.1';
    const port = Number(process.env.HORRIBLE_DEV_PORT || DEFAULT_PORT);
    const origin = `http://${host === '0.0.0.0' ? '127.0.0.1' : host}:${port}`;

    // 1. Check if backend is already running and healthy on target port
    const alreadyUp = await this.checkHealth(origin);
    if (alreadyUp) {
      this.status = {
        state: 'ready',
        origin,
        error: null,
      };
      return;
    }

    // 2. Resolve Python interpreter and working directory
    // Tier 1 (Packaged build): check bundled standalone runtime in process.resourcesPath
    // Tier 2 (Repo checkout): check venv or uv run
    const bundledRuntime = path.join(process.resourcesPath || '', 'backend-runtime');
    const bundledPython =
      process.platform === 'win32'
        ? path.join(bundledRuntime, 'python', 'python.exe')
        : path.join(bundledRuntime, 'python', 'bin', 'python');

    const venvPython = path.join(this.rootDir, '.venv', 'bin', 'python');
    const hasVenv = fs.existsSync(venvPython);

    let command: string;
    let args: string[];
    let workingDir = this.rootDir;

    if (app.isPackaged && fs.existsSync(bundledPython)) {
      command = bundledPython;
      args = ['-m', 'uvicorn', 'backend.app:app', '--host', host, '--port', String(port)];
      workingDir = bundledRuntime;
    } else if (hasVenv) {
      command = venvPython;
      args = ['-m', 'uvicorn', 'backend.app:app', '--host', host, '--port', String(port)];
    } else {
      let uvPath = 'uv';
      if (fs.existsSync('/home/horrible/.local/bin/uv')) {
        uvPath = '/home/horrible/.local/bin/uv';
      }
      command = uvPath;
      args = ['run', 'uvicorn', 'backend.app:app', '--host', host, '--port', String(port)];
    }

    // Pass environment variables, extending PATH if needed
    const env = {
      ...process.env,
      PATH: `${process.env.PATH || ''}:/home/horrible/.local/bin:/home/horrible/.cargo/bin`,
      PYTHONUNBUFFERED: '1',
    };

    try {
      this.child = spawn(command, args, {
        cwd: workingDir,
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: process.platform !== 'win32',
      });

      this.child.stderr?.on('data', (chunk: Buffer) => {
        const text = chunk.toString();
        // Log backend errors or warnings if needed
        if (process.env.ELECTRON_DEBUG_BACKEND) {
          process.stderr.write(`[backend stderr] ${text}`);
        }
      });

      this.child.on('exit', (code, signal) => {
        if (!this.shuttingDown) {
          const err = `Backend process exited prematurely with code ${code ?? 'null'} signal ${signal ?? 'null'}`;
          console.error('[backend supervisor]', err);
          this.status = {
            state: 'failed',
            origin: null,
            error: err,
          };
        }
        this.child = null;
      });
    } catch (err) {
      const errorMsg = `Failed to spawn backend process: ${err instanceof Error ? err.message : String(err)}`;
      console.error('[backend supervisor]', errorMsg);
      this.status = {
        state: 'failed',
        origin: null,
        error: errorMsg,
      };
      return;
    }

    // 3. Poll health until ready or timeout
    const deadline = Date.now() + READY_TIMEOUT_MS;
    while (Date.now() < deadline && !this.shuttingDown) {
      if (this.status.state === 'failed') return;
      const ready = await this.checkHealth(origin);
      if (ready) {
        this.status = {
          state: 'ready',
          origin,
          error: null,
        };
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }

    if (!this.shuttingDown && this.status.state !== 'ready') {
      const err = `Backend readiness timed out after ${READY_TIMEOUT_MS / 1000}s`;
      console.error('[backend supervisor]', err);
      this.status = {
        state: 'failed',
        origin: null,
        error: err,
      };
      this.shutdown();
    }
  }

  private checkHealth(origin: string): Promise<boolean> {
    return new Promise((resolve) => {
      try {
        const url = new URL('/api/health', origin);
        const req = http.get(url, { timeout: 1000 }, (res) => {
          res.resume(); // consume response body
          resolve(res.statusCode === 200);
        });
        req.on('error', () => resolve(false));
        req.on('timeout', () => {
          req.destroy();
          resolve(false);
        });
      } catch {
        resolve(false);
      }
    });
  }

  public shutdown(): void {
    this.shuttingDown = true;
    if (this.child) {
      try {
        if (process.platform !== 'win32' && this.child.pid) {
          // Kill process group
          try {
            process.kill(-this.child.pid, 'SIGTERM');
          } catch {
            this.child.kill('SIGTERM');
          }
        } else {
          this.child.kill('SIGTERM');
        }
      } catch (e) {
        console.warn('[backend supervisor] error shutting down child:', e);
      }
      this.child = null;
    }
  }
}
