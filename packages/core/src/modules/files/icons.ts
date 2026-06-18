/**
 * Lightweight, asset-free file icons: a short category-colored tag per file type
 * (styled in styles.css under `.file-icon`). A tasteful approximation of VS Code's
 * colored file icons without bundling an icon theme. Folders use a glyph in the
 * view. See docs/modules/file-explorer.md.
 */
export interface FileIcon {
  label: string;
  cls: string;
}

const BY_EXT: Record<string, FileIcon> = {
  ts: { label: 'ts', cls: 'ic-ts' },
  tsx: { label: 'tsx', cls: 'ic-ts' },
  mts: { label: 'ts', cls: 'ic-ts' },
  cts: { label: 'ts', cls: 'ic-ts' },
  js: { label: 'js', cls: 'ic-js' },
  jsx: { label: 'jsx', cls: 'ic-js' },
  mjs: { label: 'js', cls: 'ic-js' },
  cjs: { label: 'js', cls: 'ic-js' },
  py: { label: 'py', cls: 'ic-py' },
  rs: { label: 'rs', cls: 'ic-rs' },
  go: { label: 'go', cls: 'ic-go' },
  json: { label: '{}', cls: 'ic-data' },
  jsonc: { label: '{}', cls: 'ic-data' },
  yaml: { label: 'yml', cls: 'ic-data' },
  yml: { label: 'yml', cls: 'ic-data' },
  toml: { label: 'tml', cls: 'ic-data' },
  ini: { label: 'ini', cls: 'ic-data' },
  env: { label: 'env', cls: 'ic-data' },
  lock: { label: '🔒', cls: 'ic-default' },
  md: { label: 'md', cls: 'ic-doc' },
  mdx: { label: 'mdx', cls: 'ic-doc' },
  txt: { label: '¶', cls: 'ic-doc' },
  rst: { label: '¶', cls: 'ic-doc' },
  css: { label: '#', cls: 'ic-style' },
  scss: { label: '#', cls: 'ic-style' },
  less: { label: '#', cls: 'ic-style' },
  html: { label: '<>', cls: 'ic-markup' },
  xml: { label: '<>', cls: 'ic-markup' },
  svg: { label: '◆', cls: 'ic-img' },
  png: { label: '◆', cls: 'ic-img' },
  jpg: { label: '◆', cls: 'ic-img' },
  jpeg: { label: '◆', cls: 'ic-img' },
  gif: { label: '◆', cls: 'ic-img' },
  webp: { label: '◆', cls: 'ic-img' },
  ico: { label: '◆', cls: 'ic-img' },
  sh: { label: '$', cls: 'ic-script' },
  ps1: { label: '$', cls: 'ic-script' },
  bash: { label: '$', cls: 'ic-script' },
};

/** The icon tag for a file name (by extension), with a sensible default. */
export function fileIcon(name: string): FileIcon {
  const ext = name.includes('.') ? (name.split('.').pop() ?? '').toLowerCase() : '';
  return BY_EXT[ext] ?? { label: '·', cls: 'ic-default' };
}
