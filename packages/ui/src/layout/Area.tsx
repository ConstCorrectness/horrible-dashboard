/**
 * One leaf of the center grid: header (collapsible), the active pane wrapped in
 * its region strips, and the corner grip for split/join gestures. An empty area
 * renders a view picker (Blender's "pick an editor type" moment).
 */
import { layoutStore, openFramePane, registry, roleOf, type AreaNode } from '@horrible/core';

import { AreaHeader } from './AreaHeader';
import { CornerGrip } from './CornerGrip';
import { PaneWithRegions } from './Region';

function ViewPicker({ areaId }: { areaId: string }) {
  const views = [...registry.panels, ...registry.widgets].filter((v) => roleOf(v.id) !== 'tool');
  return (
    <div className="frame-view-picker">
      <div className="frame-view-picker-hint">Pick a view for this area</div>
      <div className="frame-view-picker-grid">
        {views.map((v) => (
          <button
            key={v.id}
            className="frame-view-picker-item"
            onClick={() => {
              layoutStore.dispatch({ type: 'FOCUS_AREA', areaId });
              openFramePane(v.id);
            }}
          >
            <span className="frame-view-picker-icon">{v.icon ?? v.title[0]}</span>
            <span>{v.title}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function Area({
  area,
  focused,
  fullscreen,
}: {
  area: AreaNode;
  focused: boolean;
  fullscreen?: boolean;
}) {
  const active = area.tabs[area.activeTab];
  return (
    <section
      className={`frame-area${focused ? ' frame-area--focused' : ''}${fullscreen ? ' frame-area--fullscreen' : ''}`}
      data-area-id={area.id}
      onPointerDownCapture={() => layoutStore.dispatch({ type: 'FOCUS_AREA', areaId: area.id })}
    >
      {area.headerCollapsed ? (
        <button
          className="frame-area-reveal"
          title="Show area header"
          onClick={() =>
            layoutStore.dispatch({
              type: 'SET_HEADER_COLLAPSED',
              areaId: area.id,
              collapsed: false,
            })
          }
        >
          ⌄
        </button>
      ) : (
        <AreaHeader area={area} />
      )}
      <div className="frame-area-body">
        {active ? (
          <PaneWithRegions pane={active} areaId={area.id} />
        ) : (
          <ViewPicker areaId={area.id} />
        )}
      </div>
      {!fullscreen && <CornerGrip areaId={area.id} />}
    </section>
  );
}
