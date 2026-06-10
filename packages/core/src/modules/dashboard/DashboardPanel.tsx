import { useEffect, useState } from 'react';

import { apiGet, apiPut } from '../../api';
import { hasCapability } from '../../capabilities';
import { registry, type WidgetDecl } from '../../registry';

interface DashboardLayout {
  widgets: string[];
}

function availableWidgets(): WidgetDecl[] {
  return registry.widgets.filter(
    (w) => !w.requiredCapabilities || w.requiredCapabilities.every(hasCapability),
  );
}

export function DashboardPanel() {
  const [layout, setLayout] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<DashboardLayout>('/dashboard/layout')
      .then((l) => setLayout(l.widgets))
      .catch((e: unknown) => setError(String(e)));
  }, []);

  const widgets = availableWidgets();

  const save = (ids: string[]) => {
    setLayout(ids);
    apiPut<DashboardLayout>('/dashboard/layout', { widgets: ids }).catch((e: unknown) =>
      setError(String(e)),
    );
  };

  if (error) {
    return (
      <div className="dashboard-empty">
        <p>Could not load the dashboard layout — backend unreachable.</p>
        <p className="dashboard-hint">{error}</p>
      </div>
    );
  }
  if (layout === null) return <div className="dashboard-empty">Loading…</div>;

  const shown = layout
    .map((id) => widgets.find((w) => w.id === id))
    .filter((w): w is WidgetDecl => w !== undefined);
  const addable = widgets.filter((w) => !layout.includes(w.id));

  return (
    <div className="dashboard">
      <div className="dashboard-toolbar">
        <h2>Dashboard</h2>
        {addable.length > 0 && (
          <select
            value=""
            onChange={(e) => {
              if (e.target.value) save([...layout, e.target.value]);
            }}
          >
            <option value="">Add widget…</option>
            {addable.map((w) => (
              <option key={w.id} value={w.id}>
                {w.title}
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="dashboard-grid">
        {shown.map((w) => (
          <section key={w.id} className="widget">
            <header>
              <h3>{w.title}</h3>
              <button
                aria-label={`Remove ${w.title}`}
                onClick={() => save(layout.filter((id) => id !== w.id))}
              >
                ×
              </button>
            </header>
            <div className="widget-body">
              <w.component />
            </div>
          </section>
        ))}
        {shown.length === 0 && <p className="dashboard-hint">No widgets — add one above.</p>}
      </div>
    </div>
  );
}
