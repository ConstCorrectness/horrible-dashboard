/**
 * Scrive: a Notion-like editor over MyST Markdown files on disk, and (in later
 * phases) a publisher to GitHub Pages, X, LinkedIn and YouTube. The files are the
 * source of truth — a site is a folder Jupyter Book 2 can build and git can track.
 * See docs/modules/scrive.mdx.
 */
import { focusedPane, openPane } from '../../layout/controller';
import { lazyPane } from '../../lazy-pane';
import { minibuffer } from '../../minibuffer';
import type { ModuleManifest } from '../../registry';
import { createPage } from './api';
import {
  CLIP_VIEW,
  LIVE_VIEW,
  openClip,
  openPublish,
  openScrivePage,
  openShare,
  OUTBOX_VIEW,
  PAGE_VIEW,
  PUBLISH_VIEW,
  SHARE_VIEW,
} from './open';
import { OUTLINE_VIEW } from './outline-watch';
import { LiveIndicator } from './panels/LiveIndicator';
import { OutlineIndicator } from './panels/OutlineIndicator';
import { getCurrentSite, getPageController, requestGenerate, type PageMode } from './state';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const SitesPanel = lazyPane(() => import('./panels/SitesPanel'), 'SitesPanel');
const PagePanel = lazyPane(() => import('./panels/PagePanel'), 'PagePanel');
const PostsPanel = lazyPane(() => import('./panels/PostsPanel'), 'PostsPanel');
const OutlinePanel = lazyPane(() => import('./panels/OutlinePanel'), 'OutlinePanel');
const PublishPanel = lazyPane(() => import('./panels/PublishPanel'), 'PublishPanel');
const SharePanel = lazyPane(() => import('./panels/SharePanel'), 'SharePanel');
const OutboxPanel = lazyPane(() => import('./panels/OutboxPanel'), 'OutboxPanel');
const ClipPanel = lazyPane(() => import('./panels/ClipPanel'), 'ClipPanel');
const LivePanel = lazyPane(() => import('./panels/LivePanel'), 'LivePanel');

const SITES_VIEW = 'scrive.sites';
const POSTS_VIEW = 'scrive.posts';

async function saveFocused(): Promise<void> {
  const controller = getPageController(focusedPane()?.pane.instanceId ?? null);
  if (!controller) {
    minibuffer.say('Save needs a focused Scrive page', 'error');
    return;
  }
  await controller.save();
}

async function saveFocusedAsTemplate(): Promise<void> {
  const controller = getPageController(focusedPane()?.pane.instanceId ?? null);
  if (!controller) {
    minibuffer.say('Save as template needs a focused Scrive page', 'error');
    return;
  }
  await controller.saveAsTemplate();
}

function shareFocused(): void {
  const pane = focusedPane()?.pane;
  const params = (pane?.params ?? {}) as { site?: string; path?: string };
  if (pane?.viewId !== PAGE_VIEW || !params.site || !params.path) {
    minibuffer.say('Share needs a focused Scrive page', 'error');
    return;
  }
  openShare(params.site, params.path);
}

function publishCurrent(): void {
  const site = getCurrentSite();
  if (!site) {
    openPane(SITES_VIEW);
    minibuffer.say('Pick a Scrive site to publish first', 'error');
    return;
  }
  openPublish(site);
}

function openClips(): void {
  const pane = focusedPane()?.pane;
  const params = (pane?.params ?? {}) as { site?: string };
  const site = params.site || getCurrentSite();
  if (!site) {
    openPane(SITES_VIEW);
    minibuffer.say('Pick a Scrive site first', 'error');
    return;
  }
  openClip(site);
}

function setFocusedMode(mode: PageMode): void {
  const controller = getPageController(focusedPane()?.pane.instanceId ?? null);
  if (!controller) {
    minibuffer.say('Needs a focused Scrive page', 'error');
    return;
  }
  controller.setMode(mode);
}

async function newPost(): Promise<void> {
  const site = getCurrentSite();
  if (!site) {
    openPane(SITES_VIEW);
    minibuffer.say('Create or pick a Scrive site first', 'error');
    return;
  }
  const page = await createPage(site, { kind: 'post', title: 'Untitled' });
  openScrivePage(site, page.meta.path);
}

export const scriveModule: ModuleManifest = {
  id: 'scrive',
  title: 'Scrive',
  category: 'build',
  settings: [
    {
      key: 'scrive.root',
      title: 'Scrive sites folder',
      description:
        'Where Scrive sites live. Each site is a folder of MyST Markdown pages that Jupyter Book can build and git can track.',
      type: 'string',
      default: '~/horrible/scrive',
    },
    {
      key: 'scrive.semanticSearch',
      title: 'Search sites by meaning',
      description:
        'Index pages with the embedding model the agent uses, so the agent can find a past post by what it is about, not only its words. Page text goes to that model, which may not be local. Off: keyword search only.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'scrive.x.linkInReply',
      title: 'X: post the link as a reply',
      description:
        "A thread's link to the page goes in a reply after it rather than in the first post. Each post with a link costs the URL rate either way.",
      type: 'boolean',
      default: true,
    },
    {
      key: 'scrive.youtube.audited',
      title: 'YouTube: this Cloud project passed the API audit',
      description:
        'Until it has, YouTube keeps every upload private whatever is chosen, and the composer says so.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'scrive.linkedin.version',
      title: 'LinkedIn API version',
      description:
        'The LinkedIn-Version header (YYYYMM). Each version is supported for about a year; change it deliberately when LinkedIn retires this one.',
      type: 'string',
      default: '202606',
    },
  ],
  panels: [
    {
      id: SITES_VIEW,
      title: 'Scrive',
      component: SitesPanel,
      role: 'tool',
      icon: '🪶',
      defaultDock: 'left',
      singleton: true,
    },
    {
      // Non-singleton: one pane per open page (params: {site, path, title}).
      id: PAGE_VIEW,
      title: 'Scrive Page',
      component: PagePanel,
      role: 'document',
      icon: '🪶',
    },
    {
      // The current site's posts as a table or a status board.
      id: 'scrive.posts',
      title: 'Scrive Posts',
      component: PostsPanel,
      role: 'document',
      icon: '🪶',
      singleton: true,
    },
    {
      // An agent's proposed outline, for review (params: {site, id, title}).
      id: OUTLINE_VIEW,
      title: 'Scrive Outline',
      component: OutlinePanel,
      role: 'document',
      icon: '🪶',
    },
    {
      // A site's theme, Pages target and publish button (params: {site, title}).
      id: PUBLISH_VIEW,
      title: 'Scrive Publish',
      component: PublishPanel,
      role: 'document',
      icon: '🪶',
    },
    {
      // A page's X / LinkedIn / YouTube composers (params: {site, path, title}).
      id: SHARE_VIEW,
      title: 'Scrive Share',
      component: SharePanel,
      role: 'document',
      icon: '🪶',
    },
    {
      // Every outbox row across sites, by status.
      id: OUTBOX_VIEW,
      title: 'Scrive Outbox',
      component: OutboxPanel,
      role: 'document',
      icon: '🪶',
      singleton: true,
    },
    {
      // The clip editor (params: {site, path, title}); with no path, the site's
      // clips and videos to pick from, and the screen recorder.
      id: CLIP_VIEW,
      title: 'Scrive Clip',
      component: ClipPanel,
      role: 'document',
      icon: '🪶',
    },
    {
      // A page a friend is hosting, edited live (params: {key, title}). Opened from
      // their invitation; the page itself stays on their machine.
      id: LIVE_VIEW,
      title: 'Scrive Live',
      component: LivePanel,
      role: 'document',
      icon: '🪶',
    },
  ],
  // An outline the agent proposed waits on a person; it must not get lost.
  shellIndicators: [
    { id: 'scrive.outlines', component: OutlineIndicator },
    // An invitation to edit a friend's page live arrives with no pane open.
    { id: 'scrive.live', component: LiveIndicator },
  ],
  commands: [
    { id: 'scrive.open', title: 'Scrive: Open sites', run: () => void openPane(SITES_VIEW) },
    { id: 'scrive.newPost', title: 'Scrive: New post', run: newPost },
    {
      id: 'scrive.openPosts',
      title: 'Scrive: Posts table and board',
      run: () => void openPane(POSTS_VIEW),
    },
    { id: 'scrive.save', title: 'Scrive: Save page', run: saveFocused },
    {
      id: 'scrive.generate',
      title: 'Scrive: Generate a page with the agent',
      run: () => {
        requestGenerate();
        void openPane(SITES_VIEW);
      },
    },
    { id: 'scrive.publishSite', title: 'Scrive: Publish site', run: publishCurrent },
    {
      id: 'scrive.sharePage',
      title: 'Scrive: Share page (X, LinkedIn, YouTube)',
      run: shareFocused,
    },
    { id: 'scrive.openOutbox', title: 'Scrive: Outbox', run: () => void openPane(OUTBOX_VIEW) },
    {
      id: 'scrive.openClips',
      title: 'Scrive: Clips (cut, caption, GIF, record screen)',
      run: openClips,
    },
    {
      id: 'scrive.saveAsTemplate',
      title: 'Scrive: Save page as template',
      run: saveFocusedAsTemplate,
    },
    {
      id: 'scrive.mode.write',
      title: 'Scrive: Write (blocks)',
      run: () => setFocusedMode('write'),
    },
    { id: 'scrive.mode.source', title: 'Scrive: Show source', run: () => setFocusedMode('source') },
    {
      id: 'scrive.mode.split',
      title: 'Scrive: Source beside preview',
      run: () => setFocusedMode('split'),
    },
    {
      id: 'scrive.mode.preview',
      title: 'Scrive: Show preview',
      run: () => setFocusedMode('preview'),
    },
  ],
  keybindings: [
    // More specific than the editor's unconditional mod+s, so it wins while a Scrive
    // page has focus and leaves every other pane's save alone.
    { key: 'mod+s', command: 'scrive.save', when: `paneFocus == '${PAGE_VIEW}'` },
  ],
};
