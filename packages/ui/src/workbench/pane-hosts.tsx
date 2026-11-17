/**
 * **Reparentable pane hosts**: how a pane moves into a pop-out without remounting.
 *
 * Within one document the workbench keeps panes mounted by keeping them in one flat
 * keyed layer and only restyling them (Workbench.tsx, note 1). A pop-out is another
 * document, and React cannot move a node it rendered into a different parent.
 *
 * So the pane is not rendered into its slot. Each mounted pane gets a **host
 * element** made here, once, and React renders the pane into that host through a
 * portal. The host is the one node that moves: a {@link PaneSlot} — the positioned
 * box in the main window, or the body of a pop-out — appends it. React never
 * sees the move, so the pane's state, its xterm, its WebGL context stay put.
 *
 * Moving it between documents adopts it into the new one, and observers built in
 * the main realm stop seeing it — so a move ends with `rehomeObservers()`
 * (core/layout/cross-window.ts).
 */
import { useLayoutEffect, useRef, type HTMLAttributes } from 'react';
import { rehomeObservers } from '@horrible/core';

const hosts = new Map<string, HTMLDivElement>();

/** The host element for a pane instance, made on first use. */
export function paneHost(instanceId: string): HTMLDivElement {
  let el = hosts.get(instanceId);
  if (!el) {
    el = document.createElement('div');
    el.className = 'wb-pane-host';
    el.dataset.wbPaneHost = instanceId;
    hosts.set(instanceId, el);
  }
  return el;
}

/** Drop the hosts of panes no longer mounted anywhere. */
export function prunePaneHosts(mounted: ReadonlySet<string>): void {
  for (const [id, el] of hosts) {
    if (mounted.has(id)) continue;
    el.remove();
    hosts.delete(id);
  }
}

/** A box that shows pane `instanceId` by holding its host element. */
export function PaneSlot({
  instanceId,
  ...rest
}: { instanceId: string } & HTMLAttributes<HTMLDivElement>) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const slot = ref.current;
    if (!slot) return;
    const host = paneHost(instanceId);
    if (host.parentNode === slot) return;
    const moved = host.ownerDocument !== slot.ownerDocument;
    slot.appendChild(host);
    if (moved) rehomeObservers();
    // No cleanup: the next slot to want this pane takes the host, and a pane
    // that is gone has its host pruned by the workbench.
  }, [instanceId]);
  return <div ref={ref} {...rest} />;
}
