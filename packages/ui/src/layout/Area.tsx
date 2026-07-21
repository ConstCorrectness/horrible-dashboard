/**
 * One leaf of the center grid: header (collapsible), the active pane wrapped in
 * its region strips, and the corner grip for split/join gestures. An empty area
 * renders a view picker (Blender's "pick an editor type" moment).
 */
import { useState, useSyncExternalStore } from 'react';
import {
  dropPaneOnArea,
  layoutStore,
  openPaneInArea,
  paneDrag,
  registry,
  roleOf,
  type AreaNode,
} from '@horrible/core';

import { AreaHeader } from './AreaHeader';
import { CornerGrip } from './CornerGrip';
import { PaneWithRegions } from './Region';

function ViewPicker({ areaId }: { areaId: string }) {
  // Tools last: they open in a dock by default, but an area can host them, so
  // they belong in the picker — just not ahead of the center-native views.
  const views = [...registry.panels, ...registry.widgets].sort(
    (a, b) => Number(roleOf(a.id) === 'tool') - Number(roleOf(b.id) === 'tool'),
  );
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
              // Area-targeted, not role-routed: the user picked *this* area, so a
              // tool has to land here rather than being sent off to its dock.
              openPaneInArea(v.id, areaId);
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
  const dragging = useSyncExternalStore(paneDrag.subscribe, paneDrag.getSnapshot);
  // Tracked as a counter, not a boolean: dragenter/dragleave fire for every
  // descendant the pointer crosses, so a boolean flickers off over children.
  const [over, setOver] = useState(0);
  const armed = dragging !== null;

  return (
    <section
      className={`frame-area${focused ? ' frame-area--focused' : ''}${fullscreen ? ' frame-area--fullscreen' : ''}${armed ? ' frame-area--drop-armed' : ''}${armed && over > 0 ? ' frame-area--drop-over' : ''}`}
      data-area-id={area.id}
      onPointerDownCapture={() => layoutStore.dispatch({ type: 'FOCUS_AREA', areaId: area.id })}
      onDragEnter={() => armed && setOver((n) => n + 1)}
      onDragLeave={() => armed && setOver((n) => Math.max(0, n - 1))}
      onDragOver={(e) => {
        if (!armed) return;
        // Both required, or the browser refuses the drop.
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
      }}
      onDrop={(e) => {
        if (!armed) return;
        e.preventDefault();
        setOver(0);
        dropPaneOnArea(dragging, area.id);
        paneDrag.end();
      }}
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
