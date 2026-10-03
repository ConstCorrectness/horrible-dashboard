/**
 * What Scrive's buttons say to the agent. Pure, so the wording is tested: each
 * prompt names the page and the tools, and none asks for anything to be published —
 * the agent drafts; a person publishes.
 */
import type { Outline } from './api';

/** A fence for quoting `text`: one backtick longer than any run inside it. */
export function fenceFor(text: string): string {
  const longest = Math.max(0, ...(text.match(/`+/g) ?? []).map((run) => run.length));
  return '`'.repeat(Math.max(3, longest + 1));
}

export interface GenerateRequest {
  site: string;
  request: string;
  kind: 'post' | 'page';
  template?: string;
}

/** "Generate page": plan only. The agent proposes an outline and stops. */
export function generatePrompt({ site, request, kind, template }: GenerateRequest): string {
  const using = template ? ` using the "${template}" template` : '';
  return [
    `Draft a new Scrive ${kind} in site "${site}"${using}.`,
    '',
    `Request: ${request.trim()}`,
    '',
    'Plan it first: check what the site already has (scrive.listPages, scrive.searchSite), ' +
      `then propose an outline with scrive.proposeOutline${
        template ? ` (template "${template}", with its inputs)` : ''
      } and stop. I review and approve the outline before anything is written.`,
  ].join('\n');
}

/** After "Approve and write": fill the page the outline became. */
export function fillPrompt(outline: Outline, pagePath: string): string {
  const lines = [
    `I approved the outline "${outline.title}". The page is ${outline.site}/${pagePath}.`,
    '',
    'Write it now: read the page with scrive.readPage' +
      (outline.template
        ? `, and the "${outline.template}" template's guidance (scrive.readPage with template "${outline.template}")`
        : '') +
      ', then fill each {pending} placeholder in order with scrive.fillSection, the lead ' +
      "first (heading ''). Finish with scrive.critiquePage and fix what it finds.",
  ];
  if (outline.prompt) lines.push('', `My original request: ${outline.prompt}`);
  return lines.join('\n');
}

export interface SelectionRequest {
  site: string;
  path: string;
  /** The selected blocks as MyST. */
  selection: string;
  /** The exact words selected, when that is less than the blocks. */
  excerpt?: string;
  instruction: string;
}

/** "Ask about selection": change what the instruction names, keep the rest. */
export function selectionPrompt({
  site,
  path,
  selection,
  excerpt,
  instruction,
}: SelectionRequest): string {
  const fence = fenceFor(selection);
  const words = excerpt?.trim();
  const partial = words && words !== selection.trim();
  return [
    `In Scrive page ${site}/${path}, about this selection:`,
    '',
    `${fence}myst`,
    selection.replace(/\s+$/, ''),
    fence,
    ...(partial ? ['', `The selected words: "${words}"`] : []),
    '',
    instruction.trim(),
    '',
    'Change only what this asks for and keep everything it does not mention as it is. ' +
      'Read the page first (scrive.readPage) and edit it with scrive.editPage — ' +
      'replaceText on the selected text.',
  ].join('\n');
}

/** A `{pending}` placeholder's "Write with agent". */
export function pendingPrompt(site: string, path: string, intent: string): string {
  return [
    `In Scrive page ${site}/${path}, write the section whose {pending} placeholder says:`,
    '',
    `> ${intent.trim().replace(/\n/g, '\n> ')}`,
    '',
    'Read the page (scrive.readPage) to find its heading, then write it with ' +
      'scrive.fillSection. Leave the other placeholders alone.',
  ].join('\n');
}
