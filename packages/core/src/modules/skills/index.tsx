/**
 * Skills module: reusable instructions in Anthropic's SKILL.md format.
 *
 * The human surface only. The backend (`backend/modules/skills/`) owns the files and
 * feeds the agent through the same progressive-disclosure path the tool groups use —
 * a cheap description every turn, the instructions only when `use_skill` asks. See
 * docs/modules/skills.mdx.
 */
import { registry, type ModuleManifest } from '../../registry';
import { listSkills, skillCost, summarize } from './api';
import { SkillsPane } from './panels/SkillsPane';

export const skillsModule: ModuleManifest = {
  id: 'skills',
  title: 'Skills',
  panels: [
    {
      id: 'skills.library',
      title: 'Skills',
      component: SkillsPane,
      // A browsable library — the same shape as the marketplace, which is a
      // document. It was a dock-only `tool`, which put a catalogue you read and
      // edit into a 280px rail. `dockable` keeps the rail glyph for consulting
      // it alongside something else.
      role: 'document',
      icon: '🎓',
      dockable: 'left',
      singleton: true,
      /**
       * Library and editor are **sections**, not a `useState` swap of the pane
       * body.
       *
       * The editor used to replace the whole pane from local state, which meant it
       * had no address: nothing could link to it, the tab strip did not know it was
       * open, reloading dropped you back in the list, and the only way out was the
       * component's own Cancel button. A section is persisted with the layout and
       * reachable from the palette, which is what MCP's three sections already do.
       */
      sections: [
        { id: 'library', label: 'Library', icon: '📚', key: 'l', default: true },
        { id: 'editor', label: 'Editor', icon: '✎', key: 'e' },
      ],
    },
  ],
  commands: [
    {
      id: 'skills.open',
      title: 'Skills: Open',
      run: () => registry.openPanel('skills.library'),
    },
    {
      id: 'skills.new',
      title: 'Skills: New skill',
      /**
       * Opens the pane and switches it to the editor. It used to call `openPanel`
       * and stop, landing you in the library with no form in sight — the command's
       * title was a promise it did not keep.
       *
       * Runs the registry-synthesized section command rather than reaching for
       * `setPaneSection`: UI calls commands, never the reverse, which is the same
       * rule `/mcp open` follows.
       */
      run: async () => {
        registry.openPanel('skills.library');
        await registry.runCommand('section.show:skills.library:editor');
      },
    },
    {
      id: 'skills.status',
      title: 'Skills: Show what the agent knows',
      // Carries the per-turn cost, not just the list: "what do my skills cost me" is
      // the question the pane exists for, and the palette should answer it too.
      run: async () => {
        const [{ skills }, cost] = await Promise.all([listSkills(), skillCost()]);
        return summarize(skills, cost);
      },
    },
  ],
};

export { listSkills, skillCost, summarize } from './api';
export type { Skill, SkillCost, SkillInput, SkillPreview, SkillScope } from './api';
