/**
 * The pane's icon set: stroke glyphs that inherit `currentColor`, so a button
 * colours its icon by setting its own text colour and nothing has to be restyled
 * per state. No emoji inside the pane — the manifest's rail glyph is the one
 * documented exception (see CLAUDE.local.md).
 */
import type { ReactNode } from 'react';

interface IconProps {
  size?: number;
}

function svg(size: number, children: ReactNode) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flexShrink: 0 }}
    >
      {children}
    </svg>
  );
}

export function BookIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M4 5a2 2 0 0 1 2-2h11v18H6a2 2 0 0 1-2-2Z" />
      <path d="M8 3v18" />
    </>,
  );
}

export function PlusIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>,
  );
}

export function RefreshIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M20 11a8 8 0 0 0-13.7-5.3L4 8" />
      <path d="M4 4v4h4" />
      <path d="M4 13a8 8 0 0 0 13.7 5.3L20 16" />
      <path d="M20 20v-4h-4" />
    </>,
  );
}

export function TrashIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M4 7h16" />
      <path d="M9 7V5h6v2" />
      <path d="M6 7l1 12h10l1-12" />
    </>,
  );
}

export function ExternalIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M14 4h6v6" />
      <path d="M20 4 11 13" />
      <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </>,
  );
}

export function SearchIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="m20 20-4.5-4.5" />
    </>,
  );
}

export function BackIcon({ size = 14 }: IconProps) {
  return svg(size, <path d="M15 5 8 12l7 7" />);
}
