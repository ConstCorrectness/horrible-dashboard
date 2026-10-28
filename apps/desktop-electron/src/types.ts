export interface BackendStatus {
  state: 'starting' | 'ready' | 'failed' | 'unavailable';
  origin: string | null;
  error: string | null;
}

export type ResizeEdge =
  | 'east'
  | 'west'
  | 'north'
  | 'south'
  | 'north-east'
  | 'north-west'
  | 'south-east'
  | 'south-west';

export interface WebviewBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type BrowserWebviewEvent =
  | { kind: 'load'; id: string; url: string; loading: boolean }
  | { kind: 'title'; id: string; title: string }
  | { kind: 'newTab'; id: string; url: string }
  | {
      kind: 'download';
      id: string;
      url: string;
      path: string | null;
      state: 'started' | 'done' | 'failed';
    };

export interface BrowserCdpEvent {
  id: string;
  method: string;
  params: Record<string, unknown>;
}
