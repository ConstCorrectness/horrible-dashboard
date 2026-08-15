import { apiGet } from '../../api';

/** Which of the three resolution rules produced a root — see backend/paths.py. */
export type RootSource = 'environment' | 'checkout' | 'platform';

export interface StorageRoot {
  id: string;
  title: string;
  path: string;
  /** A root is created on first write, so `false` is normal, not an error. */
  exists: boolean;
  source: RootSource;
  /** The environment variable that overrides this root. */
  envVar: string;
  note: string;
}

export interface StoragePaths {
  roots: StorageRoot[];
  /** The checkout this node is running from; empty in a packaged install. */
  repo: string;
}

export const getPaths = () => apiGet<StoragePaths>('/paths');
