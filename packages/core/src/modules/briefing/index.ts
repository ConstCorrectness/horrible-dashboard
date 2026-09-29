import { type ModuleManifest, registry } from '../../registry';
import { loadBriefing } from './store';

export const BRIEFING_IN_SPOTLIGHT_KEY = 'briefing.showInSpotlight';

/**
 * The AI briefing: the week's most-upvoted papers and the last two days of AI
 * headlines, shown in Spotlight's empty state beside quick settings and
 * notifications.
 *
 * It contributes **no pane**. The briefing is a glance you take on the way to
 * something else, and Spotlight is already the surface you open on the way to
 * something else — a pane would be a destination nobody navigates to. The data
 * comes from `backend/modules/briefing/`. See docs/modules/briefing.mdx.
 */
export const briefingModule: ModuleManifest = {
  id: 'briefing',
  title: 'AI Briefing',
  category: 'system',
  commands: [
    {
      id: 'briefing.show',
      title: 'AI Briefing: Show',
      // The briefing lives in Spotlight's empty state, so showing it is opening
      // Spotlight — routed through the command so a rebind of `mod+k` still wins.
      run: () => registry.runCommand('shell.commandPalette'),
    },
    {
      id: 'briefing.refresh',
      title: 'AI Briefing: Refresh',
      run: () => loadBriefing({ force: true }),
    },
  ],
  settings: [
    {
      key: BRIEFING_IN_SPOTLIGHT_KEY,
      title: 'Show the AI briefing in Spotlight',
      description:
        'When Spotlight opens with nothing typed, show top papers, AI headlines, quick settings and notifications. Off shows the plain command list.',
      type: 'boolean',
      default: true,
    },
  ],
};

export {
  arxivAbsUrl,
  arxivPdfUrl,
  hfPaperUrl,
  saveBriefingPaper,
  type BriefingPaper,
  type BriefingStory,
} from './api';
export {
  FRESH_MS as BRIEFING_FRESH_MS,
  getBriefing,
  loadBriefing,
  subscribeBriefing,
  useBriefing,
  type BriefingSection,
  type BriefingState,
} from './store';
