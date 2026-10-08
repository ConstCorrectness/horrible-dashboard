/**
 * `{space}`: Space ids and URLs, the embed-host allowlist, the Hub lookup, and what
 * the app and a published page render (the frame behind a closed `<details>`).
 */
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { parseMyst } from '../myst/parse';
import { MystView } from '../render/MystView';
import { fetchSpaceInfo, parseSpaceRef, spaceEmbedUrl, spacePageUrl } from '../render/space';
import { StaticRenderContext, type StaticRender } from '../render/static-context';

describe('parseSpaceRef', () => {
  it.each([
    ['webml-community/smollm-webgpu', 'webml-community/smollm-webgpu'],
    ['  Logolabs/agate-webgpu  ', 'Logolabs/agate-webgpu'],
    [
      'https://huggingface.co/spaces/webml-community/gemma-4-webgpu-kernels',
      'webml-community/gemma-4-webgpu-kernels',
    ],
    ['https://huggingface.co/spaces/a/b/tree/main', 'a/b'],
  ])('%s', (input, id) => {
    expect(parseSpaceRef(input)).toEqual({ id });
  });

  it.each(['', 'just-a-name', 'https://evil.example/spaces/a/b', 'https://huggingface.co/a/b', 42])(
    'refuses %s',
    (input) => {
      expect(parseSpaceRef(input)).toBeNull();
    },
  );
});

describe('spaceEmbedUrl', () => {
  it('accepts only hf.space hosts, always over https', () => {
    expect(spaceEmbedUrl('webml-community-smollm-webgpu.static.hf.space')).toBe(
      'https://webml-community-smollm-webgpu.static.hf.space',
    );
    expect(spaceEmbedUrl('https://abc-def.hf.space/')).toBe('https://abc-def.hf.space');
    for (const bad of [
      'evil.example',
      'x.hf.space.evil.example',
      'http://a.hf.space',
      'a.b.hf.space',
      '',
      null,
    ]) {
      expect(spaceEmbedUrl(bad), String(bad)).toBeNull();
    }
  });

  it('links the Space page', () => {
    expect(spacePageUrl('a/b')).toBe('https://huggingface.co/spaces/a/b');
  });
});

describe('fetchSpaceInfo', () => {
  it('reads the embed host and card from the Hub API', async () => {
    const fetcher = (async () =>
      new Response(
        JSON.stringify({
          id: 'webml-community/bonsai-webgpu-kernels',
          host: 'https://webml-community-bonsai-webgpu-kernels.static.hf.space',
          subdomain: 'webml-community-bonsai-webgpu-kernels',
          sdk: 'static',
          likes: 467,
          runtime: { stage: 'RUNNING' },
          cardData: { title: 'Bonsai 27B WebGPU Kernels', emoji: '🌳', license: 'apache-2.0' },
        }),
      )) as typeof fetch;
    await expect(fetchSpaceInfo('webml-community/bonsai-webgpu-kernels', fetcher)).resolves.toEqual(
      {
        id: 'webml-community/bonsai-webgpu-kernels',
        host: 'https://webml-community-bonsai-webgpu-kernels.static.hf.space',
        sdk: 'static',
        title: 'Bonsai 27B WebGPU Kernels',
        emoji: '🌳',
        likes: 467,
        stage: 'RUNNING',
        license: 'apache-2.0',
      },
    );
  });

  it('says so when the Space does not exist, and does not cache the failure', async () => {
    let calls = 0;
    const fetcher = (async () => {
      calls++;
      return new Response('{}', { status: 404 });
    }) as typeof fetch;
    await expect(fetchSpaceInfo('no/such', fetcher)).rejects.toThrow('no Space no/such');
    await expect(fetchSpaceInfo('no/such', fetcher)).rejects.toThrow();
    expect(calls).toBe(2);
  });
});

const RUN = {
  model: 'onnx-community/Qwen3-0.6B-ONNX',
  prompt: 'Say hi',
  steps: [
    {
      token: 'Hi',
      p: 0.9,
      entropy: 0.4,
      topk: [
        { token: 'Hi', p: 0.9 },
        { token: 'Hello', p: 0.05 },
      ],
    },
    { token: '!', p: 0.3, entropy: 2.1, topk: [] },
  ],
};

const published: StaticRender = {
  asset: (_p: string, url: string) => url,
  link: (_p: string, href: string) => href,
  cellOutputs: () => [],
  mermaid: () => null,
  scene: () => '',
  app: (_p: string, name: string) => `../../_scrive/apps/${name}/`,
  webllm: (_p: string, params: Record<string, unknown>) =>
    `../../_scrive/webml/embed.html#${encodeURIComponent(JSON.stringify(params))}`,
  data: (_p: string, src: string) => (src === 'data/run.json' ? RUN : undefined),
};

function render(src: string, statik = false): string {
  const tree = <MystView tree={parseMyst(src)} site="blog" pagePath="posts/p.md" lineOffset={0} />;
  return renderToStaticMarkup(
    statik ? (
      <StaticRenderContext.Provider value={published}>{tree}</StaticRenderContext.Provider>
    ) : (
      tree
    ),
  );
}

describe('rendering', () => {
  const page =
    '```{space} webml-community/smollm-webgpu\n:height: 500\n:host: webml-community-smollm-webgpu.static.hf.space\n```';

  it('a published page ships the frame, lazily, inside a closed <details>', () => {
    const html = render(page, true);
    expect(html).toContain('<details class="scrive-run">');
    expect(html).toContain('src="https://webml-community-smollm-webgpu.static.hf.space"');
    expect(html).toContain('loading="lazy"');
    expect(html).not.toContain('<details class="scrive-run" open');
    expect(html).toContain('https://huggingface.co/spaces/webml-community/smollm-webgpu');
  });

  it('the app creates no frame until the reader opens it', () => {
    const html = render(page);
    expect(html).toContain('scrive-run');
    expect(html).not.toContain('<iframe');
  });

  it('a host that is not an hf.space is never embedded', () => {
    const html = render('```{space} a/b\n:host: evil.example\n```', true);
    expect(html).not.toContain('<iframe');
    expect(html).toContain('huggingface.co/spaces/a/b');
  });

  it('explains a bad argument instead of dropping the block', () => {
    expect(render('```{space} not a space\n```')).toContain('needs a Space id');
  });
});

describe('{app}', () => {
  it('opens as an embed block and prints back as written', async () => {
    const { editableBlock, printBlock } = await import('../myst/pm');
    const src = '```{app} kernel-demo\n:height: 500\n```';
    const pm = editableBlock(parseMyst(src).children[0], src);
    expect(pm?.type).toBe('scriveEmbed');
    expect(pm?.attrs).toMatchObject({
      name: 'app',
      src: 'kernel-demo',
      options: { height: '500' },
    });
    expect(printBlock(pm!)).toBe(src);
  });

  it('a published page embeds the copy under _scrive/apps/', () => {
    const html = render('```{app} apps/kernel-demo/\n:height: 500\n```', true);
    expect(html).toContain('src="../../_scrive/apps/kernel-demo/"');
    expect(html).toContain('height:500px');
  });

  it('a published page drops an {app} that names no app', () => {
    expect(render('```{app} ../../etc\n```', true)).not.toContain('<iframe');
  });
});

describe('{webllm} and {tokenviz}', () => {
  it('both open as embed blocks and print back as written', async () => {
    const { editableBlock, printBlock } = await import('../myst/pm');
    for (const src of [
      '```{webllm} onnx-community/Qwen3-0.6B-ONNX\n:system: Be terse.\n:show: chat, tokens\n```',
      '```{tokenviz} ../data/run.json\n```',
    ]) {
      const pm = editableBlock(parseMyst(src).children[0], src);
      expect(pm?.type, src).toBe('scriveEmbed');
      expect(printBlock(pm!)).toBe(src);
    }
  });

  it('a published {webllm} embeds the model page with its settings in the fragment', () => {
    const html = render(
      '```{webllm} onnx-community/Qwen3-0.6B-ONNX\n:system: Be terse.\n:show: chat, tokens\n:max: 64\n```',
      true,
    );
    const src = /src="([^"]+)"/.exec(html)?.[1] ?? '';
    expect(src.startsWith('../../_scrive/webml/embed.html#')).toBe(true);
    expect(JSON.parse(decodeURIComponent(src.split('#')[1].replace(/&amp;/g, '&')))).toEqual({
      model: 'onnx-community/Qwen3-0.6B-ONNX',
      dtype: '',
      system: 'Be terse.',
      tokens: true,
      lens: false,
      max: 64,
      sizes: { q4f16: 569_789_750, q4: 919_096_585 },
    });
    expect(html).toContain('loading="lazy"');
  });

  it('a published GGUF {webllm} passes its id and the lens through to the page', () => {
    const html = render(
      '```{webllm} gguf:Qwen/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q8_0.gguf\n:show: lens\n```',
      true,
    );
    const src = /src="([^"]+)"/.exec(html)?.[1] ?? '';
    expect(JSON.parse(decodeURIComponent(src.split('#')[1].replace(/&amp;/g, '&')))).toMatchObject({
      model: 'gguf:Qwen/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q8_0.gguf',
      tokens: true,
      lens: true,
      // Not in the ONNX catalog: the page asks the Hub for the file's size.
      sizes: {},
    });
  });

  it('a published {tokenviz} is the strip as plain markup, from preloaded data', () => {
    const html = render('```{tokenviz} data/run.json\n```', true);
    expect(html).toContain('class="tokstrip"');
    expect(html).toContain('2 tokens');
    expect(html).toContain('Say hi');
    expect(html).toContain('&quot;Hello&quot;');
    // A missing file drops the figure for readers rather than showing an error.
    expect(render('```{tokenviz} data/missing.json\n```', true)).not.toContain('tokenviz');
  });
});

describe('parseTokenRun', () => {
  it('accepts a run and reports what is wrong with anything else', async () => {
    const { parseTokenRun } = await import('../../../token-strip/TokenStrip');
    expect(parseTokenRun(RUN)).toMatchObject({
      model: RUN.model,
      steps: [{ token: 'Hi' }, { token: '!' }],
    });
    expect(parseTokenRun(null)).toBe('not a JSON object');
    expect(parseTokenRun({})).toBe('no "steps" array');
    expect(parseTokenRun({ steps: [{ token: 1 }] })).toBe('a step needs "token" and "p"');
  });

  it('keeps a step’s logit lens when every layer is well-formed, and drops it otherwise', async () => {
    const { parseTokenRun } = await import('../../../token-strip/TokenStrip');
    const layers = [
      { norm: 3.5, entropy: 9.1, top: [{ token: 'the', p: 0.02 }] },
      { norm: 41, entropy: 0.6, top: [{ token: 'Hi', p: 0.88 }] },
    ];
    const step = { token: 'Hi', p: 0.9, entropy: 0.4, topk: [] };
    const run = (l: unknown) => parseTokenRun({ steps: [{ ...step, layers: l }] });
    expect(run(layers)).toMatchObject({ steps: [{ layers }] });
    expect((run([{ norm: 'x' }]) as { steps: object[] }).steps[0]).not.toHaveProperty('layers');
    expect((run([]) as { steps: object[] }).steps[0]).not.toHaveProperty('layers');
  });

  it('draws the lens in the token’s hover card as plain markup, the chosen token marked', async () => {
    const { TokenStrip } = await import('../../../token-strip/TokenStrip');
    const html = renderToStaticMarkup(
      <TokenStrip
        steps={[
          {
            token: 'Hi',
            p: 0.9,
            entropy: 0.4,
            topk: [],
            layers: [
              { norm: 3.5, entropy: 9.1, top: [{ token: 'the', p: 0.02 }] },
              { norm: 41, entropy: 0.6, top: [{ token: 'Hi', p: 0.88 }] },
            ],
          },
        ]}
      />,
    );
    expect(html).toContain('aria-label="Logit lens by layer"');
    expect(html.match(/tokstrip-lens-row/g)).toHaveLength(2);
    expect(html).toContain('tokstrip-lens-row is-chosen');
    // Norm bars are relative to the step's largest.
    expect(html).toContain('width:100%');
  });
});

describe('{webllm} options', () => {
  it('lens in :show: asks for the logit lens and shows the strip it lives in', async () => {
    const { webLlmOptions } = await import('../render/WebLlm');
    expect(webLlmOptions('m', { show: 'chat, lens' })).toMatchObject({
      showTokens: true,
      showLens: true,
    });
    expect(webLlmOptions('m', { show: 'chat tokens' })).toMatchObject({
      showTokens: true,
      showLens: false,
    });
  });
});
