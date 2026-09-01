/**
 * The **research** module: store and read research material.
 *
 * Frontend half of `backend/modules/research` + `backend/modules/artifacts`:
 * a PDF viewer and a captured-page viewer over the artifact store, plus the
 * commands that get material in (capture a URL as a self-contained page, upload
 * or fetch a PDF). Deep-research runs and the ArXiv browser land in later
 * phases of the same module. See docs/modules/research.mdx.
 */
import { dialogs } from '../../dialogs';
import { registry, type ModuleManifest } from '../../registry';
import { toastsStore } from '../../toasts';
import { getSetting } from '../../settings';
import { captureUrl, savePdfUrl, uploadPdf } from './api';
import { ArxivPanel } from './panels/ArxivPanel';
import { PageViewer } from './panels/PageViewer';
import { PdfViewer } from './panels/PdfViewer';
import { ResearchConsole } from './panels/ResearchConsole';

function saveLibrary(): string {
  return getSetting<string>('browser.saveLibrary') || 'default';
}

async function capturePageCommand(): Promise<void> {
  const url = await dialogs.prompt({
    title: 'Capture page',
    placeholder: 'https://… (saved as a self-contained archive + library source)',
    confirmLabel: 'Capture',
  });
  const trimmed = url?.trim();
  if (!trimmed) return;
  toastsStore.add('info', 'Capturing…', trimmed);
  try {
    const res = await captureUrl(trimmed, { library: saveLibrary() });
    toastsStore.add('success', 'Page captured', res.source.title);
    registry.openPanel('research.pageViewer', {
      instanceId: `page:${res.artifact.id}`,
      params: { artifactId: res.artifact.id, sourceId: res.source.id },
    });
  } catch (err) {
    toastsStore.add('warning', 'Capture failed', err instanceof Error ? err.message : String(err));
  }
}

async function savePdfUrlCommand(): Promise<void> {
  const url = await dialogs.prompt({
    title: 'Save PDF by URL',
    placeholder: 'https://…/paper.pdf',
    confirmLabel: 'Save',
  });
  const trimmed = url?.trim();
  if (!trimmed) return;
  toastsStore.add('info', 'Fetching PDF…', trimmed);
  try {
    const res = await savePdfUrl(trimmed, { library: saveLibrary() });
    toastsStore.add('success', 'PDF saved', res.source.title);
    registry.openPanel('research.pdfViewer', {
      instanceId: `pdf:${res.artifact.id}`,
      params: { artifactId: res.artifact.id, sourceId: res.source.id },
    });
  } catch (err) {
    toastsStore.add('warning', 'Save failed', err instanceof Error ? err.message : String(err));
  }
}

function openPdfCommand(): void {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'application/pdf,.pdf';
  input.onchange = async () => {
    const file = input.files?.[0];
    if (!file) return;
    toastsStore.add('info', 'Uploading…', file.name);
    try {
      const res = await uploadPdf(file, { library: saveLibrary() });
      toastsStore.add('success', 'PDF stored', res.source.title);
      registry.openPanel('research.pdfViewer', {
        instanceId: `pdf:${res.artifact.id}`,
        params: { artifactId: res.artifact.id, sourceId: res.source.id },
      });
    } catch (err) {
      toastsStore.add('warning', 'Upload failed', err instanceof Error ? err.message : String(err));
    }
  };
  input.click();
}

export const researchModule: ModuleManifest = {
  id: 'research',
  title: 'Research',
  panels: [
    {
      id: 'research.pdfViewer',
      title: 'PDF',
      component: PdfViewer,
      role: 'document',
      icon: '📕',
      // Non-singleton: read several papers side by side. Openers pass
      // `{ artifactId | sourceId | url, page? }` and an `instanceId` keyed on the
      // artifact so re-opening the same paper focuses the existing pane.
    },
    {
      id: 'research.pageViewer',
      title: 'Saved page',
      component: PageViewer,
      role: 'document',
      icon: '📄',
    },
    {
      id: 'research.arxiv',
      title: 'ArXiv',
      component: ArxivPanel,
      role: 'document',
      icon: '🎓',
      // One search surface; papers open as their own pdfViewer panes.
      singleton: true,
    },
    {
      id: 'research.console',
      title: 'Deep Research',
      component: ResearchConsole,
      role: 'document',
      icon: '🔬',
      // One console managing every run (runs live server-side, not per-pane).
      singleton: true,
      // UI-side tools only: run/capture tools are backend AgentTools (they must
      // work with no browser attached); these open viewers for the human.
      agentTools: [
        {
          name: 'research.openConsole',
          description:
            'Open the Deep Research console pane in the UI so the user can watch a run (plan, steps, streaming synthesis) live.',
          params: { type: 'object', properties: {} },
          sideEffect: true,
          handler: async () => {
            registry.openPanel('research.console');
            return { opened: true };
          },
        },
        {
          name: 'research.openPdf',
          description:
            'Open a stored PDF in the PDF viewer pane. Pass the artifact_id (from research.savePdf / arxiv.download / library search hits).',
          params: {
            type: 'object',
            properties: {
              artifactId: { type: 'string', description: 'The PDF artifact id.' },
              page: { type: 'integer', description: 'Page to jump to.' },
            },
            required: ['artifactId'],
          },
          sideEffect: true,
          handler: async (args) => {
            const { artifactId, page } = args as { artifactId: string; page?: number };
            registry.openPanel('research.pdfViewer', {
              instanceId: `pdf:${artifactId}`,
              params: { artifactId, page },
            });
            return { opened: true };
          },
        },
        {
          name: 'research.openPage',
          description:
            'Open a captured page (self-contained HTML artifact) in the sandboxed page viewer. Pass the artifact_id from research.capture or a page-source search hit.',
          params: {
            type: 'object',
            properties: {
              artifactId: { type: 'string', description: 'The page artifact id.' },
            },
            required: ['artifactId'],
          },
          sideEffect: true,
          handler: async (args) => {
            const { artifactId } = args as { artifactId: string };
            registry.openPanel('research.pageViewer', {
              instanceId: `page:${artifactId}`,
              params: { artifactId },
            });
            return { opened: true };
          },
        },
      ],
    },
  ],
  commands: [
    {
      id: 'research.capturePage',
      title: 'Research: Capture page by URL',
      run: () => void capturePageCommand(),
    },
    {
      id: 'research.openPdf',
      title: 'Research: Open PDF file…',
      run: () => openPdfCommand(),
    },
    {
      id: 'research.savePdfUrl',
      title: 'Research: Save PDF by URL',
      run: () => void savePdfUrlCommand(),
    },
    {
      id: 'research.openArxiv',
      title: 'Research: Open ArXiv browser',
      run: () => registry.openPanel('research.arxiv'),
    },
    {
      id: 'research.openConsole',
      title: 'Research: Open deep-research console',
      run: () => registry.openPanel('research.console'),
    },
  ],
  settings: [
    {
      key: 'research.obsidianVault',
      title: 'Obsidian vault path',
      description:
        'Absolute path of an Obsidian vault to export captures, PDFs, and research reports into (empty disables export).',
      type: 'string',
      default: '',
    },
    {
      key: 'research.obsidianFolder',
      title: 'Obsidian folder',
      description: 'Folder inside the vault that exports are written to.',
      type: 'string',
      default: 'Horrible Research',
    },
    {
      key: 'research.singleFileCli',
      title: 'single-file-cli executable',
      description:
        'Optional: name/path of a user-installed single-file CLI to use for URL captures instead of the built-in inliner. SingleFile is AGPL, so it is never bundled — installing it is your call; its fetches are its own (only the initial URL is validated).',
      type: 'string',
      default: '',
    },
    {
      key: 'research.provider',
      title: 'Deep-research provider',
      description:
        'Model provider for deep-research runs. Empty uses the agent’s configured provider (local by default). Cloud providers (openai/anthropic/gemini) need their API key connected — keys live in the encrypted secrets store, never in settings.',
      type: 'enum',
      enumValues: ['', 'ollama', 'lmstudio', 'vllm', 'openai', 'anthropic', 'gemini'],
      default: '',
    },
    {
      key: 'research.model',
      title: 'Deep-research model',
      description: 'Lead model for research runs. Empty uses the agent’s configured model.',
      type: 'string',
      default: '',
    },
    {
      key: 'research.subagentModel',
      title: 'Subagent model',
      description:
        'Model for the parallel research subagents. Empty uses the lead model — a smaller/faster model here cuts cost with little quality loss.',
      type: 'string',
      default: '',
    },
    {
      key: 'research.maxSubagents',
      title: 'Max subagents',
      description: 'Upper bound on parallel subagents a deep run may plan.',
      type: 'number',
      default: 4,
    },
    {
      key: 'research.subagentParallelism',
      title: 'Subagent parallelism',
      description:
        'How many subagents run concurrently. Local providers serialize poorly under load — keep this low for Ollama.',
      type: 'number',
      default: 2,
    },
    {
      key: 'research.distributeSubagents',
      title: 'Distribute subagents to friends',
      description:
        "Hand some of each research wave to a trusted friend's agent. A peer runs under THEIR permission mode (read-only by default), so a remote subagent may have fewer tools than a local one — and one that declines or times out simply runs here instead.",
      type: 'boolean',
      default: false,
    },
    {
      key: 'research.tokenBudget',
      title: 'Run token budget',
      description:
        'Approximate token ceiling per run; past it, remaining subagents are skipped and the run synthesizes what it has.',
      type: 'number',
      default: 200000,
    },
    {
      key: 'research.webSearch',
      title: 'Web search',
      description:
        'Let research subagents search the open web. Uses whichever engines are configured in the Web Search module — with no API key and no SearXNG instance it falls back to a keyless DuckDuckGo scrape, which may rate-limit. Off means runs rely on arXiv, the local index, the library, and fetch-by-URL.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'research.maxRounds',
      title: 'Max research rounds',
      description:
        'Override the number of gap-filling rounds a run may spend. After each round the lead reviews what came back and either stops or spawns subagents targeting what is still missing. 0 (the default) uses the effort tier: quick 1, standard 2, deep 3.',
      type: 'number',
      default: 0,
    },
    {
      key: 'research.verifyDepth',
      title: 'Claim verification',
      description:
        '“cheap” extracts the report’s load-bearing claims and checks how many independent publishers back each one (three citations to the same site count as one source), then flags contradictions between sources. “off” skips the audit and its two model calls. “corroborate” additionally re-searches single-sourced claims.',
      type: 'enum',
      enumValues: ['off', 'cheap', 'corroborate'],
      default: 'cheap',
    },
    {
      key: 'research.maxVerifiedClaims',
      title: 'Max verified claims',
      description: 'Upper bound on how many claims the verification pass audits per report.',
      type: 'number',
      default: 12,
    },
    {
      key: 'research.maxConcurrentRuns',
      title: 'Concurrent runs',
      description: 'How many research runs execute at once (the rest queue).',
      type: 'number',
      default: 1,
    },
  ],
};
