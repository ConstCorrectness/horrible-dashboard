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
      // The form lives in the pane; the command is the discoverable way to reach it.
      run: () => registry.openPanel('skills.library'),
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
