/**
 * The module shell: what `horrible run <id>` opens. One module package, full-window,
 * no desktop.
 *
 * Boot is the dashboard's, cut down to what a module needs: settings, the theme, the
 * module packages the slim backend says this process runs (`/api/modules` reports
 * only those as enabled), and the `/ws` socket their sessions broadcast on. Then the
 * module's `standalone.pane` renders through the same `PaneHost` the desktop uses.
 */
import { StrictMode, useSyncExternalStore } from 'react';
import { createRoot } from 'react-dom/client';
import {
  initTheme,
  loadModulePackages,
  loadSettings,
  moduleLoadErrors,
  registry,
  resolveView,
  useModulePackages,
} from '@horrible/core';
import { StandalonePane } from '@horrible/ui';
import { modules } from 'virtual:horrible-modules';

const root = createRoot(document.getElementById('root')!);

function Message({ title, detail }: { title: string; detail: string }) {
  return (
    <div style={{ padding: '2rem', fontFamily: 'var(--font-mono, monospace)' }}>
      <h1
        style={{
          fontSize: '0.9rem',
          textTransform: 'uppercase',
          letterSpacing: '0.14em',
          margin: '0 0 0.6rem',
        }}
      >
        {title}
      </h1>
      <p style={{ color: 'var(--text-secondary)', margin: 0 }}>{detail}</p>
    </div>
  );
}

/**
 * Live, not rendered once: the module can come and go while the page is open. If the
 * dashboard holds it when this runner starts, the runner defers it; when the
 * dashboard lets go, the backend loads it here, the loader registers the web half,
 * and this swaps the message for the pane without a reload.
 */
function Shell({ moduleId, viewId, name }: { moduleId: string; viewId: string; name: string }) {
  const snapshot = useModulePackages();
  useSyncExternalStore(
    (fn) => registry.onChange(fn),
    () => registry.has(moduleId),
  );
  if (resolveView(viewId)) return <StandalonePane viewId={viewId} />;
  const state = snapshot.modules.find((m) => m.id === moduleId);
  const failed = moduleLoadErrors.find((e) => e.moduleId === moduleId);
  return (
    <Message
      title={`${name} is not running here`}
      detail={state?.error ?? failed?.message ?? (snapshot.ready ? `It has no pane "${viewId}".` : 'Connecting…')}
    />
  );
}

async function boot(): Promise<void> {
  const params = new URLSearchParams(location.search);
  const backend = await fetch('/api/health')
    .then((r) => r.json() as Promise<{ modules?: string[] }>)
    .catch(() => null);
  const moduleId = params.get('module') ?? backend?.modules?.[0] ?? '';
  const entry = modules.find((m) => m.manifest.id === moduleId);
  if (!entry) {
    root.render(<Message title="No such module" detail={`No module package "${moduleId}".`} />);
    return;
  }
  await loadModulePackages(modules);
  await loadSettings();
  initTheme();

  const manifest = entry.manifest as {
    name?: string;
    standalone?: { pane?: string; title?: string };
  };
  const viewId = params.get('pane') ?? manifest.standalone?.pane ?? '';
  const name = manifest.name ?? moduleId;
  document.title = manifest.standalone?.title ?? name;
  root.render(
    <StrictMode>
      <Shell moduleId={moduleId} viewId={viewId} name={name} />
    </StrictMode>,
  );
}

void boot();
