import { useCallback, useEffect, useState } from 'react';

import type { PluginPackageManifest } from '@horribledashboard/sdk';

import { CopyableValue } from '../../CopyableLink';
import { openExternal } from '../../external';
import { IconAlert, IconRetry } from '../../glyphs';
import { pluginLoadErrors, type InstalledPlugin } from '../../plugins/loader';
import { Button, EmptyState } from '../../Primitives';
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

/** The published plugin guide. The empty state's one way forward, so it goes
 *  through `openExternal`, which reports a failure the browser swallows. */
const PLUGIN_GUIDE_URL =
  'https://constcorrectness.github.io/horrible-dashboard/architecture/plugin-sdk';

function openPluginDocs(): Promise<boolean> {
  return openExternal(PLUGIN_GUIDE_URL);
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
        {/* A security statement, not filler. It used to share `.dashboard-hint`
            with empty-state copy and descriptions, which made the one sentence
            carrying real risk look like the least important text on the pane. */}
        <span className="mk-trust">
          <IconAlert />
          Plugins run unsandboxed, with the same access the app has. Install only what you trust.
        </span>
      </div>

      {dirty && (
        <div className="mk-banner">
          {/* Reloading discards live pane state — a terminal's PTY, a notebook
              kernel, an open match. The banner says so rather than presenting a
              full page reload as a routine confirmation. */}
          <div className="mk-banner-text">
            <span className="mk-banner-title">Reload to apply plugin changes</span>
            <p className="mk-banner-body">
              Reloading restarts the interface. Panes holding live sessions — terminals, notebook
              kernels, running matches — will be closed.
            </p>
          </div>
          <Button intent="primary" size="sm" onClick={() => window.location.reload()}>
            Reload now
          </Button>
        </div>
      )}

      {error && (
        <div className="mk-alert" data-kind="fail" role="alert">
          <span className="mk-alert-title">Couldn’t read the catalog</span>
          <p className="mk-alert-body">{error}</p>
          <Button intent="primary" size="sm" icon={<IconRetry />} onClick={() => void refresh()}>
            Try again
          </Button>
        </div>
      )}

      {pluginLoadErrors.length > 0 && (
        <div className="mk-alert" data-kind="warn" role="alert">
          <span className="mk-alert-title">
            {pluginLoadErrors.length === 1
              ? '1 installed plugin failed to load'
              : `${pluginLoadErrors.length} installed plugins failed to load`}
          </span>
          <p className="mk-alert-body">
            They are installed but not running. The rest of the app is unaffected.
          </p>
          <ul className="mk-alert-list">
            {pluginLoadErrors.map((e) => (
              <li key={e.pluginId}>
                <span className="mk-alert-id">{e.pluginId}</span>
                <span className="mk-alert-reason">{e.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {rows.length === 0 && !error && (
        // First use and "the catalog is empty" are the same screen here, so it
        // says what a catalog *is* and hands over the exact value to set —
        // selectable, with a copy button, rather than a name to retype.
        <EmptyState
          title="No plugins yet"
          actions={
            <Button intent="primary" size="sm" onClick={() => void openPluginDocs()}>
              Read the plugin guide
            </Button>
          }
        >
          <p>
            A catalog is a directory of plugin packages this node can install from. Point the
            backend at one and reopen this pane.
          </p>
          <CopyableValue label="Environment variable" value="HORRIBLE_PLUGIN_CATALOG" />
        </EmptyState>
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
