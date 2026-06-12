import { useCallback, useEffect, useState } from 'react';

import type { PluginPackageManifest } from '@horrible/sdk';

import { pluginLoadErrors, type InstalledPlugin } from '../../plugins/loader';
import {
  getInstalledPlugins,
  getPluginCatalog,
  installPlugin,
  setPluginEnabled,
  uninstallPlugin,
} from './api';

interface Row {
  id: string;
  catalog: PluginPackageManifest | null;
  installed: InstalledPlugin | null;
}

function mergeRows(catalog: PluginPackageManifest[], installed: InstalledPlugin[]): Row[] {
  const byId = new Map<string, Row>();
  for (const manifest of catalog) {
    byId.set(manifest.id, { id: manifest.id, catalog: manifest, installed: null });
  }
  for (const plugin of installed) {
    const row = byId.get(plugin.manifest.id);
    if (row) row.installed = plugin;
    else byId.set(plugin.manifest.id, { id: plugin.manifest.id, catalog: null, installed: plugin });
  }
  return [...byId.values()].sort((a, b) => a.id.localeCompare(b.id));
}

/**
 * Browse the plugin catalog and manage installed plugins. Lifecycle changes
 * (install/update/enable/disable/uninstall) take effect on the next reload —
 * the registry has no unregister, so the panel offers a reload banner instead.
 */
export function MarketplacePanel() {
  const [catalog, setCatalog] = useState<PluginPackageManifest[]>([]);
  const [installed, setInstalled] = useState<InstalledPlugin[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [cat, inst] = await Promise.all([getPluginCatalog(), getInstalledPlugins()]);
      setCatalog(cat.plugins);
      setInstalled(inst.plugins);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = useCallback(
    async (id: string, action: () => Promise<unknown>) => {
      setBusyId(id);
      try {
        await action();
        setDirty(true);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyId(null);
      }
    },
    [refresh],
  );

  const rows = mergeRows(catalog, installed);

  return (
    <div className="mk-panel">
      <div className="dashboard-toolbar">
        <h2>Marketplace</h2>
        <span className="dashboard-hint">
          Plugins are trusted code — install only what you trust.
        </span>
      </div>

      {dirty && (
        <div className="mk-banner">
          Plugin changes apply after a reload.
          <button className="primary" onClick={() => window.location.reload()}>
            Reload now
          </button>
        </div>
      )}

      {error && <div className="widget-error">{error}</div>}

      {pluginLoadErrors.length > 0 && (
        <div className="widget-error">
          {pluginLoadErrors.map((e) => (
            <div key={e.pluginId}>
              {e.pluginId} failed to load: {e.message}
            </div>
          ))}
        </div>
      )}

      {rows.length === 0 && !error && (
        <p className="dashboard-hint">
          No plugins in the catalog. Point HORRIBLE_PLUGIN_CATALOG at a directory of plugin
          packages.
        </p>
      )}

      <ul className="mk-list">
        {rows.map((row) => {
          const manifest = row.installed?.manifest ?? row.catalog;
          if (!manifest) return null;
          const busy = busyId === row.id;
          const updateAvailable =
            row.installed && row.catalog && row.installed.manifest.version !== row.catalog.version;
          return (
            <li key={row.id} className="mk-item">
              <div className="mk-item-info">
                <div className="mk-item-head">
                  <strong>{manifest.name}</strong>
                  <span className="dashboard-hint">
                    v{manifest.version}
                    {updateAvailable && row.catalog ? ` → v${row.catalog.version}` : ''}
                    {manifest.author ? ` · ${manifest.author}` : ''}
                  </span>
                  {row.installed && (
                    <span className={`mk-badge ${row.installed.enabled ? 'mk-on' : 'mk-off'}`}>
                      {row.installed.enabled ? 'enabled' : 'disabled'}
                    </span>
                  )}
                </div>
                <span className="dashboard-hint">{manifest.description}</span>
                {manifest.permissions.length > 0 && (
                  <span className="dashboard-hint">
                    permissions: {manifest.permissions.join(', ')}
                  </span>
                )}
              </div>
              <div className="mk-item-actions">
                {!row.installed && row.catalog && (
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => void act(row.id, () => installPlugin(row.id))}
                  >
                    Install
                  </button>
                )}
                {updateAvailable && (
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => void act(row.id, () => installPlugin(row.id))}
                  >
                    Update
                  </button>
                )}
                {row.installed && (
                  <>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void act(row.id, () => setPluginEnabled(row.id, !row.installed?.enabled))
                      }
                    >
                      {row.installed.enabled ? 'Disable' : 'Enable'}
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => void act(row.id, () => uninstallPlugin(row.id))}
                    >
                      Uninstall
                    </button>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
