import type * as Preset from '@docusaurus/preset-classic';
import type { Config } from '@docusaurus/types';
import { themes as prismThemes } from 'prism-react-renderer';

// The docs site is a standalone sub-project: it renders the repo's top-level
// docs/ (the docs-next-to-code source of truth) as a static website. Content
// lives in ../docs; only theming/config lives here. See docs/README.mdx.
const config: Config = {
  title: 'horrible-dashboard',
  tagline: 'emacs for the agentic era',

  // GitHub Pages project page: https://constcorrectness.github.io/horrible-dashboard/
  url: 'https://constcorrectness.github.io',
  baseUrl: '/horrible-dashboard/',
  organizationName: 'ConstCorrectness',
  projectName: 'horrible-dashboard',
  trailingSlash: false,

  onBrokenLinks: 'throw',

  markdown: {
    mermaid: true,
    // .mdx → strict MDX (JSX/expressions); .md → lenient CommonMark. Lets the
    // few not-yet-migrated pages build while migrated .mdx pages get full MDX.
    format: 'detect',
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },
  themes: ['@docusaurus/theme-mermaid'],

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          // Render the repo's top-level docs/ as the whole site.
          path: '../docs',
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/ConstCorrectness/horrible-dashboard/tree/main/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'horrible-dashboard',
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://github.com/ConstCorrectness/horrible-dashboard',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            { label: 'Architecture', to: '/architecture/layout-shell' },
            { label: 'Modules', to: '/modules/agent-chat' },
          ],
        },
        {
          title: 'More',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/ConstCorrectness/horrible-dashboard',
            },
          ],
        },
      ],
      copyright: `Built with Docusaurus. © ${new Date().getFullYear()} horrible-dashboard.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'json', 'python', 'toml'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
