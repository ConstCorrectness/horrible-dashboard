/**
 * Skills API client — SKILL.md files the agent can read.
 *
 * The shape worth understanding before reading the pane: a skill has two tiers, and
 * they cost completely different things. `description` is injected into every single
 * turn (it is how the model knows the skill exists), while `body` reaches the model
 * only when it calls `use_skill`. Everything in the UI is arranged around making that
 * asymmetry visible, because it is the whole reason skills are affordable at all.
 */
import { apiDelete, apiGet, apiPost } from '../../api';

/** Where a skill's file lives. `project` skills are the repo's, and are read-only here. */
export type SkillScope = 'user' | 'project';

/** One file inside a skill's directory, named relative to it. */
export interface SkillFile {
  /** `references/rules.md` — relative, because that is what the skill's body links to. */
  name: string;
  bytes: number;
}

export interface Skill {
  name: string;
  /** Rides EVERY turn. This is the trigger, and the per-turn cost. */
  description: string;
  /** Delivered only when the model calls `use_skill`. */
  body: string;
  /** Tool names or group names the skill needs; activated when it's used. */
  allowedTools: string[];
  scope: SkillScope;
  path: string;
  /** Why this skill can't be used. Empty when it can. */
  error: string;
  /** A project skill hidden by a user skill of the same name. */
  shadowed: boolean;
  enabled: boolean;
  /** Everything in the skill's directory, `SKILL.md` first. */
  files: SkillFile[];
}

export interface SkillList {
  skills: Skill[];
  userDir: string;
  projectDir: string;
}

export interface SkillCost {
  skills: Array<{ name: string; tokens: number; bodyTokens: number }>;
  /** What every turn pays for skills, before the model has done anything. */
  catalogTokens: number;
  /** False means chars/4 estimates — the pane says so rather than implying precision. */
  exact: boolean;
  tokenizer: string;
}

export interface SkillPreview {
  /** The literal system message injected each turn. */
  catalog: string;
  /** The literal text `use_skill` returns. */
  instructions: string;
  groups: string[];
}

export interface SkillFileContent {
  name: string;
  bytes: number;
  text: string;
}

export interface SkillInput {
  name: string;
  description: string;
  body: string;
  allowedTools: string[];
}

export function listSkills(): Promise<SkillList> {
  return apiGet<SkillList>('/skills');
}

export function saveSkill(input: SkillInput): Promise<Skill> {
  return apiPost<Skill>('/skills', input);
}

export function deleteSkill(name: string): Promise<SkillList> {
  return apiDelete<SkillList>(`/skills/${encodeURIComponent(name)}`);
}

export function setSkillEnabled(name: string, enabled: boolean): Promise<Skill> {
  return apiPost<Skill>(`/skills/${encodeURIComponent(name)}/enabled`, { enabled });
}

export function skillCost(): Promise<SkillCost> {
  return apiGet<SkillCost>('/skills/cost');
}

export function skillPreview(name: string): Promise<SkillPreview> {
  return apiGet<SkillPreview>(`/skills/${encodeURIComponent(name)}/preview`);
}

/**
 * One resource file's text.
 *
 * Read-only, and deliberately so: the editor writes `SKILL.md` and nothing else, which
 * is exactly what the backend's `save` does. A viewer that could write would need the
 * whole question of what happens to a project skill's tracked files answered first.
 */
export function readSkillFile(name: string, file: string): Promise<SkillFileContent> {
  const path = file
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/');
  return apiGet<SkillFileContent>(`/skills/${encodeURIComponent(name)}/files/${path}`);
}

/** Copy a project skill into your own, so it becomes editable. */
export function copySkill(name: string): Promise<Skill> {
  return apiPost<Skill>(`/skills/${encodeURIComponent(name)}/copy`, {});
}

/** Copy a skill into `.claude/skills/` so Claude Code picks it up unchanged. */
export function exportSkill(name: string): Promise<{ path: string }> {
  return apiPost<{ path: string }>(`/skills/${encodeURIComponent(name)}/export`, {});
}

/**
 * A one-line-per-skill summary, shared by the `skills.status` command and any chat
 * surface. Lives here so the two can't disagree about what "enabled" means.
 */
export function summarize(skills: Skill[], cost?: SkillCost | null): string {
  if (skills.length === 0) {
    return 'No skills yet. Create one with the "Skills: New skill" command.';
  }
  const lines = skills.map((s) => {
    if (s.error) return `✕ ${s.name} — ${s.error}`;
    if (s.shadowed) return `○ ${s.name} — shadowed by your own skill of the same name`;
    if (!s.enabled) return `○ ${s.name} — disabled`;
    return `● ${s.name} — ${s.description}`;
  });
  const head =
    cost && cost.catalogTokens > 0
      ? `Skills (${skills.length}) — ${cost.catalogTokens} tokens every turn:`
      : `Skills (${skills.length}):`;
  return [head, ...lines].join('\n');
}
