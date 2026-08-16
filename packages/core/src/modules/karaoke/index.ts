/**
 * Karaoke module: a PiKaraoke-shaped karaoke machine as a dashboard workspace.
 *
 * Three panes and one frame. The split is not cosmetic — it's the shape of a
 * karaoke night: a **stage** that faces the room, a **queue** that says who's up,
 * and a **search** pane that fills the queue. The Karaoke frame puts the stage in
 * the center with the other two in docks, which is the whole layout a host needs.
 *
 * The interesting half is on the backend: the session (queue, now-playing,
 * transport, key) is process-global and broadcast on the `karaoke` `/ws` channel,
 * so these panes are renderers, not owners. That is what makes a phone on the LAN
 * a working remote for free, and what makes the agent's `karaoke.*` tools work
 * with no pane open at all. See docs/modules/karaoke.mdx.
 */
import './karaoke.css';

import { registry, type ModuleManifest } from '../../registry';
import { KaraokeQueuePanel } from './panels/QueuePanel';
import { KaraokeSearchPanel } from './panels/SearchPanel';
import { KaraokeStagePanel } from './panels/StagePanel';
import { applyPlayerState, connectKaraoke, ensureLoaded, getPlayerState } from './store';
import { nextSong, pause, play, restart, setTranspose } from './api';

/** Nudge the key, clamped to the ±6 the backend accepts. */
async function transposeBy(delta: number): Promise<void> {
  const current = getPlayerState().semitones;
  const next = Math.max(-6, Math.min(6, current + delta));
  if (next !== current) applyPlayerState(await setTranspose(next));
}

export const karaokeModule: ModuleManifest = {
  id: 'karaoke',
  title: 'Karaoke',
  panels: [
    {
      id: 'karaoke.stage',
      title: 'Karaoke',
      component: KaraokeStagePanel,
      role: 'document',
      // The stage is pointed at a room, not at the person driving it. Chrome
      // around the lyrics is chrome the singers are reading past.
      fullscreen: true,
      icon: '🎤',
      singleton: true,
      // Takes the keyboard so the transport bindings below reach it and the
      // shell's own single-letter bindings don't fire while the host is driving
      // the player. Same reasoning as the game and terminal panes.
      capture: { mode: 'keyboard' },
    },
    {
      id: 'karaoke.queue',
      title: 'Queue',
      component: KaraokeQueuePanel,
      role: 'tool',
      icon: '📋',
      defaultDock: 'left',
      defaultDockSize: 300,
      singleton: true,
    },
    {
      id: 'karaoke.search',
      title: 'Find songs',
      component: KaraokeSearchPanel,
      role: 'tool',
      icon: '🔎',
      defaultDock: 'right',
      defaultDockSize: 340,
      singleton: true,
    },
  ],
  commands: [
    {
      id: 'karaoke.open',
      title: 'Karaoke: Open stage',
      run: () => {
        connectKaraoke();
        void ensureLoaded();
        registry.openPanel('karaoke.stage');
      },
    },
    {
      id: 'karaoke.findSongs',
      title: 'Karaoke: Find songs',
      run: () => registry.openPanel('karaoke.search'),
    },
    {
      id: 'karaoke.showQueue',
      title: 'Karaoke: Show queue',
      run: () => registry.openPanel('karaoke.queue'),
    },
    {
      id: 'karaoke.playPause',
      title: 'Karaoke: Play / pause',
      run: async () => {
        const playing = getPlayerState().playing;
        applyPlayerState(await (playing ? pause() : play()));
      },
    },
    {
      id: 'karaoke.next',
      title: 'Karaoke: Next singer',
      run: async () => applyPlayerState(await nextSong()),
    },
    {
      id: 'karaoke.restart',
      title: 'Karaoke: Restart song',
      run: async () => applyPlayerState(await restart()),
    },
    {
      id: 'karaoke.keyUp',
      title: 'Karaoke: Raise the key',
      run: () => transposeBy(1),
    },
    {
      id: 'karaoke.keyDown',
      title: 'Karaoke: Lower the key',
      run: () => transposeBy(-1),
    },
  ],
  keybindings: [
    // All scoped to the stage: these are single keys, and unscoped they would
    // steal Space and the arrows from every text field in the app.
    { key: 'space', command: 'karaoke.playPause', when: "paneFocus == 'karaoke.stage'" },
    { key: 'n', command: 'karaoke.next', when: "paneFocus == 'karaoke.stage'" },
    { key: 'r', command: 'karaoke.restart', when: "paneFocus == 'karaoke.stage'" },
    { key: 'up', command: 'karaoke.keyUp', when: "paneFocus == 'karaoke.stage'" },
    { key: 'down', command: 'karaoke.keyDown', when: "paneFocus == 'karaoke.stage'" },
  ],
  settings: [
    {
      key: 'karaoke.volume',
      title: 'Default volume',
      description: 'Playback volume the session starts at, 0 to 1.',
      type: 'number',
      default: 0.85,
    },
    {
      key: 'karaoke.searchResults',
      title: 'Search results',
      description: 'How many YouTube hits the search pane asks for.',
      type: 'number',
      default: 20,
    },
  ],
  frames: [
    {
      id: 'karaoke',
      name: 'Karaoke',
      icon: '🎤',
      frame: {
        center: { pane: 'karaoke.stage' },
        docks: {
          left: { tools: ['karaoke.queue'], size: 300 },
          right: { tools: ['karaoke.search'], size: 340 },
        },
      },
    },
  ],
};
