import { describe, expect, it } from 'vitest';

import { researchModule } from '../index';

describe('research module', () => {
  it('declares the panes with the right multiplicity', () => {
    const byId = new Map((researchModule.panels ?? []).map((p) => [p.id, p]));
    for (const panel of byId.values()) expect(panel.role).toBe('document');
    // Viewers are multi-instance: read several papers side by side.
    expect(byId.get('research.pdfViewer')?.singleton).toBeFalsy();
    expect(byId.get('research.pageViewer')?.singleton).toBeFalsy();
    // One search surface / one run manager.
    expect(byId.get('research.arxiv')?.singleton).toBe(true);
    expect(byId.get('research.console')?.singleton).toBe(true);
  });

  it('declares the capture/open/save/console commands', () => {
    const commandIds = (researchModule.commands ?? []).map((c) => c.id);
    expect(commandIds).toContain('research.capturePage');
    expect(commandIds).toContain('research.openPdf');
    expect(commandIds).toContain('research.savePdfUrl');
    expect(commandIds).toContain('research.openArxiv');
    expect(commandIds).toContain('research.openConsole');
  });

  it('exposes UI-opening agent tools on the console pane only', () => {
    const console = (researchModule.panels ?? []).find((p) => p.id === 'research.console');
    const names = (console?.agentTools ?? []).map((t) => t.name);
    expect(names).toEqual(['research.openConsole', 'research.openPdf', 'research.openPage']);
    for (const tool of console?.agentTools ?? []) expect(tool.sideEffect).toBe(true);
  });

  it('declares the Obsidian + single-file-cli settings with safe defaults', () => {
    const settings = researchModule.settings ?? [];
    const byKey = (k: string) => settings.find((s) => s.key === k);
    expect(byKey('research.obsidianVault')?.default).toBe(''); // export off until configured
    expect(byKey('research.obsidianFolder')?.default).toBe('Horrible Research');
    expect(byKey('research.singleFileCli')?.default).toBe(''); // AGPL CLI is opt-in
  });
});
