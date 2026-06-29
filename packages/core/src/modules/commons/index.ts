import { registry, type ModuleManifest } from '../../registry';
import { CommonsDirectory } from './CommonsDirectory';
import { CommonsProfileEditor } from './CommonsProfileEditor';
import { CommonsRequests } from './CommonsRequests';

/**
 * The **agent commons**, frontend side: browse/search other nodes' public profiles, the
 * two-sided consent handshake, a reputation floor (block/vouch/report + trust tiers),
 * a profile editor, and an inbound-requests inbox — all over the `/ws` `commons` channel.
 * Profiles are built + signed on the backend (the node key never leaves it). See
 * docs/modules/commons.mdx and docs/architecture/agent-commons.mdx.
 */
export const commonsModule: ModuleManifest = {
  id: 'commons',
  title: 'Commons',
  widgets: [
    {
      id: 'commons.directory',
      title: 'Commons',
      component: CommonsDirectory,
      defaultPlacement: 'right',
    },
    {
      id: 'commons.requests',
      title: 'Commons Requests',
      component: CommonsRequests,
      defaultPlacement: 'right',
    },
    {
      id: 'commons.profile',
      title: 'Commons Profile',
      component: CommonsProfileEditor,
      defaultPlacement: 'right',
    },
  ],
  commands: [
    {
      id: 'commons.open',
      title: 'Commons: Open directory',
      run: () => registry.openPanel('commons.directory'),
    },
    {
      id: 'commons.openRequests',
      title: 'Commons: Open requests',
      run: () => registry.openPanel('commons.requests'),
    },
    {
      id: 'commons.openProfile',
      title: 'Commons: Edit my profile',
      run: () => registry.openPanel('commons.profile'),
    },
  ],
  settings: [
    {
      key: 'commons.enabled',
      title: 'Enable the agent commons',
      description: 'Connect to a commons index at startup to discover and be discovered.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'commons.serverUrl',
      title: 'Commons server URL',
      description: 'The ws://…/commons-ws index to connect to (blank = off).',
      type: 'string',
      default: '',
    },
    {
      key: 'commons.autoPublish',
      title: 'Publish my profile automatically',
      description: 'On connect, publish this node’s signed profile so others can find it.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'commons.headline',
      title: 'Profile headline',
      description: 'One line on what you / your agent do — the storefront tagline.',
      type: 'string',
      default: '',
    },
    {
      key: 'commons.bio',
      title: 'Profile bio',
      description: 'A longer description shown on your commons profile.',
      type: 'string',
      default: '',
    },
    {
      key: 'commons.tags',
      title: 'Profile tags',
      description: 'Comma-separated skills/interests (e.g. rust, data-viz, trading).',
      type: 'string',
      default: '',
    },
    {
      key: 'commons.seeking',
      title: 'Looking for',
      description: 'What collaboration you’re looking for (optional).',
      type: 'string',
      default: '',
    },
    {
      key: 'commons.visibility',
      title: 'Profile visibility',
      description: 'public = listed in the directory; unlisted = reachable by link only.',
      type: 'enum',
      default: 'public',
      enumValues: ['public', 'unlisted'],
    },
  ],
};

export {
  initCommons,
  getCommonsState,
  subscribeCommons,
  commonsConnect,
  commonsSearch,
  commonsRefresh,
  commonsPublish,
  commonsRequest,
  commonsRespond,
  commonsBlock,
  commonsUnblock,
  commonsVouch,
  commonsReport,
  commonsSetProfile,
  type CommonsProfile,
  type CommonsCandidate,
  type CommonsRequest,
  type CommonsState,
} from './commons';
