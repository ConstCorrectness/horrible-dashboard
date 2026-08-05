// @vitest-environment happy-dom
/**
 * The provider registry's keying rule: **pane instance + section**, not instance
 * alone. A section body renders inside its host's `PaneInstanceContext`, so under
 * the old flat key two providers on one pane silently overwrote each other — last
 * mount won, and unmounting it restored nothing. Every case here failed that way
 * before the split.
 *
 * Imports `../agent-context` directly rather than the package barrel: the barrel
 * pulls in module manifests that reach the editor, which opens a WebSocket at
 * module scope.
 */
import { act, useState, type ReactElement, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  hasAgentContext,
  PaneInstanceContext,
  readAgentContext,
  SectionInstanceContext,
  sectionsWithAgentContext,
  useAgentContext,
} from '../agent-context';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function render(node: ReactElement): void {
  act(() => {
    root.render(node);
  });
}

/** A pane host: supplies the instance id, and a section slot for a section body. */
function Pane({
  instanceId,
  section,
  children,
}: {
  instanceId: string;
  section?: string;
  children: ReactNode;
}) {
  return (
    <PaneInstanceContext.Provider value={instanceId}>
      <SectionInstanceContext.Provider value={section ?? null}>
        {children}
      </SectionInstanceContext.Provider>
    </PaneInstanceContext.Provider>
  );
}

function Body({ snapshot, section }: { snapshot: Record<string, unknown>; section?: string }) {
  useAgentContext(() => snapshot, section);
  return null;
}

beforeEach(() => {
  container = document.createElement('div');
  root = createRoot(container);
});

// The provider registry is module-global; leaving a root mounted leaks its
// registrations into the next case (which is how the first draft of this file
// "failed" — every assertion saw the previous test's pane).
afterEach(() => {
  act(() => {
    root.unmount();
  });
});

describe('agent context keying', () => {
  it('keeps a pane-level and a section provider side by side', () => {
    render(
      <Pane instanceId="p1">
        <Body snapshot={{ pane: 'yes', shared: 'pane' }} />
        <Pane instanceId="p1" section="files">
          <Body snapshot={{ files: 3, shared: 'section' }} />
        </Pane>
      </Pane>,
    );

    // Merged, section last: the more specific value wins the shared key.
    expect(readAgentContext('p1')).toEqual({ pane: 'yes', files: 3, shared: 'section' });
    expect(sectionsWithAgentContext('p1')).toEqual(['files']);
  });

  it('does not let a second section clobber the first', () => {
    render(
      <Pane instanceId="p1">
        <Pane instanceId="p1" section="files">
          <Body snapshot={{ from: 'files' }} />
        </Pane>
        <Pane instanceId="p1" section="notebooks">
          <Body snapshot={{ from: 'notebooks' }} />
        </Pane>
      </Pane>,
    );

    // Reading a *named* section ignores the other — a switch that leaves a stale
    // provider mounted must not answer for the tab that replaced it.
    expect(readAgentContext('p1', 'files')).toEqual({ from: 'files' });
    expect(readAgentContext('p1', 'notebooks')).toEqual({ from: 'notebooks' });
    expect(sectionsWithAgentContext('p1').sort()).toEqual(['files', 'notebooks']);
  });

  it('leaves the survivors registered when one section unmounts', () => {
    function Switcher({ show }: { show: boolean }) {
      return (
        <Pane instanceId="p1">
          <Body snapshot={{ pane: 'yes' }} />
          {show && (
            <Pane instanceId="p1" section="files">
              <Body snapshot={{ from: 'files' }} />
            </Pane>
          )}
        </Pane>
      );
    }
    render(<Switcher show />);
    expect(readAgentContext('p1')).toEqual({ pane: 'yes', from: 'files' });

    render(<Switcher show={false} />);
    // The pane-level provider survives its section going away — under the old flat
    // key the section had overwritten it and took it down on unmount.
    expect(readAgentContext('p1')).toEqual({ pane: 'yes' });
    expect(sectionsWithAgentContext('p1')).toEqual([]);
  });

  it('reports context for a pane whose only provider is a section', () => {
    render(
      <Pane instanceId="p1" section="files">
        <Body snapshot={{ from: 'files' }} />
      </Pane>,
    );
    // `listOpenPanes` reports this flag; a section-only pane is still readable.
    expect(hasAgentContext('p1')).toBe(true);
    expect(readAgentContext('p1', 'notebooks')).toBeNull();
  });

  it('drops the pane entirely once its last provider unmounts', () => {
    function Maybe({ show }: { show: boolean }) {
      return show ? (
        <Pane instanceId="p1" section="files">
          <Body snapshot={{ from: 'files' }} />
        </Pane>
      ) : null;
    }
    render(<Maybe show />);
    expect(hasAgentContext('p1')).toBe(true);
    render(<Maybe show={false} />);
    expect(hasAgentContext('p1')).toBe(false);
    expect(readAgentContext('p1')).toBeNull();
  });

  it('re-registers under the new section when a body changes section', () => {
    function Movable() {
      const [section, setSection] = useState('files');
      return (
        <Pane instanceId="p1" section={section}>
          <Body snapshot={{ section }} />
          <button onClick={() => setSection('notebooks')}>go</button>
        </Pane>
      );
    }
    render(<Movable />);
    expect(sectionsWithAgentContext('p1')).toEqual(['files']);

    act(() => {
      container.querySelector('button')!.click();
    });
    // The old key is released, not left behind as a phantom section.
    expect(sectionsWithAgentContext('p1')).toEqual(['notebooks']);
  });
});
