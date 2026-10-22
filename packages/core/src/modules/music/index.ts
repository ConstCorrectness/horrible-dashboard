/**
 * Music module: the app's own music library, and a player that plays it.
 *
 * Karaoke's sibling for normal listening. The library is authoritative — every
 * song is a file this app owns, downloaded (yt-dlp, audio only) or copied in from
 * this machine — and Spotify, Apple Music exports, Last.fm and ListenBrainz only
 * *feed* it: playlists, metadata, scrobbles, "similar to". Nothing plays in
 * Spotify's app.
 *
 * One pane with two strips (library left, queue right), a board widget, and a
 * now-playing chip in the shell. The audio itself belongs to none of them: it's
 * an app-wide element (player.ts) started at boot by {@link initMusic}, so music
 * keeps playing through tab, workspace and desktop switches, and the agent's
 * `music.*` tools work with no pane open. See docs/modules/music.mdx.
 */
import { lazyPane } from '../../lazy-pane';
import './music.css';

import { registry, type ModuleManifest } from '../../registry';
import { next, pause, previous, setRepeat, setShuffle } from './api';
import { MusicConnectionsSection } from './ConnectionsSection';
import { MusicNowPlayingIndicator, MusicNowPlayingWidget } from './NowPlaying';
import { playHere, startMusicPlayer } from './player';
import { connectMusic, ensureLoaded, getPlayerState } from './store';
import { act } from './ui';

const MusicPlayerPanel = lazyPane(() => import('./panels/PlayerPanel'), 'MusicPlayerPanel');
const MusicLibraryPanel = lazyPane(() => import('./panels/LibraryPanel'), 'MusicLibraryPanel');
const MusicQueuePanel = lazyPane(() => import('./panels/QueuePanel'), 'MusicQueuePanel');

const REPEAT_CYCLE = { off: 'all', all: 'one', one: 'off' } as const;

/**
 * Start following the music session in this window: the `music` channel, the
 * saved session, and the app-wide audio element. Called once at boot, like
 * `initAudio` — a player that only woke up when its pane mounted would leave
 * an agent's "play something" silent until somebody opened it.
 */
export function initMusic(): void {
  connectMusic();
  void ensureLoaded();
  startMusicPlayer();
}

export const musicModule: ModuleManifest = {
  id: 'music',
  title: 'Music',
  category: 'play',
  panels: [
    {
      id: 'music.player',
      title: 'Music',
      component: MusicPlayerPanel,
      role: 'document',
      icon: '🎵',
      singleton: true,
      regions: [
        {
          id: 'music.library',
          label: 'Library',
          icon: '≡',
          position: 'left',
          defaultOpen: true,
          defaultSize: 360,
        },
        {
          id: 'music.queue',
          label: 'Up next',
          icon: '⋮',
          position: 'right',
          defaultOpen: true,
          defaultSize: 300,
        },
      ],
    },
    {
      id: 'music.library',
      title: 'Music library',
      component: MusicLibraryPanel,
      role: 'tool',
      icon: '📚',
      singleton: true,
      embedded: true,
    },
    {
      id: 'music.queue',
      title: 'Up next',
      component: MusicQueuePanel,
      role: 'tool',
      icon: '📋',
      singleton: true,
      embedded: true,
    },
    {
      id: 'music.nowPlaying',
      title: 'Now playing',
      component: MusicNowPlayingWidget,
      role: 'widget',
      icon: '🎵',
      singleton: true,
    },
  ],
  shellIndicators: [{ id: 'music.nowPlaying', component: MusicNowPlayingIndicator }],
  commands: [
    {
      id: 'music.open',
      title: 'Music: Open player',
      run: () => {
        initMusic();
        registry.openPanel('music.player');
      },
    },
    {
      id: 'music.playPause',
      title: 'Music: Play / pause',
      run: async () => {
        const state = getPlayerState();
        if (state.playing) {
          await act(pause(), 'Pause');
        } else {
          await act(playHere(), 'Play');
        }
      },
    },
    { id: 'music.next', title: 'Music: Next song', run: () => act(next(), 'Next') },
    { id: 'music.previous', title: 'Music: Previous song', run: () => act(previous(), 'Previous') },
    {
      id: 'music.toggleShuffle',
      title: 'Music: Toggle shuffle',
      run: () => act(setShuffle(!getPlayerState().shuffle), 'Shuffle'),
    },
    {
      id: 'music.cycleRepeat',
      title: 'Music: Cycle repeat (off → all → one)',
      run: () => act(setRepeat(REPEAT_CYCLE[getPlayerState().repeat]), 'Repeat'),
    },
    {
      id: 'music.showLibrary',
      title: 'Music: Open library',
      run: () => registry.openPanel('music.library'),
    },
    {
      id: 'music.showQueue',
      title: 'Music: Show up next',
      run: () => registry.openPanel('music.queue'),
    },
  ],
  keybindings: [
    // Scoped to the player: single keys, and unscoped they'd steal Space and the
    // arrows from every text field. Media keys reach the player globally through
    // navigator.mediaSession instead.
    { key: 'space', command: 'music.playPause', when: "paneFocus == 'music.player'" },
    { key: 'n', command: 'music.next', when: "paneFocus == 'music.player'" },
    { key: 'p', command: 'music.previous', when: "paneFocus == 'music.player'" },
    { key: 's', command: 'music.toggleShuffle', when: "paneFocus == 'music.player'" },
    { key: 'r', command: 'music.cycleRepeat', when: "paneFocus == 'music.player'" },
  ],
  settings: [
    {
      key: 'music.scrobble',
      title: 'Scrobble',
      description: 'Send what you play to Last.fm and ListenBrainz, when connected.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'music.autoEnrich',
      title: 'Tidy up downloads',
      description: 'Look new songs up on MusicBrainz for clean titles, albums and cover art.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'music.radioDownloads',
      title: 'Radio may download',
      description:
        'Let radio and the DJ fetch similar songs you don’t have yet, not only pick from the library.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'music.minMatchConfidence',
      title: 'Import match threshold',
      description:
        'How sure (0–1) a playlist import must be that a download is the right song. Below it, the song waits for your review.',
      type: 'number',
      default: 0.6,
      advanced: true,
    },
  ],
  settingsSections: [
    { id: 'music.connections', title: 'Music connections', component: MusicConnectionsSection },
  ],
};

