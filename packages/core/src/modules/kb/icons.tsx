/**
 * Knowledge pane icons: stroke glyphs that inherit `currentColor`, so a control
 * colours its icon through its own text colour. No emoji inside the pane — the
 * manifest's rail glyph is the one documented exception (CLAUDE.local.md).
 */
import type { ReactNode } from 'react';

interface IconProps {
  size?: number;
  filled?: boolean;
}

function svg(size: number, children: ReactNode, filled = false) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? 'currentColor' : 'none'}
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

export function SearchIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.2-4.2" />
    </>,
  );
}

export function ImportIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M12 3v12" />
      <path d="m7 10 5 5 5-5" />
      <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </>,
  );
}

export function ExternalIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M14 4h6v6" />
      <path d="M20 4 11 13" />
      <path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
    </>,
  );
}

export function OfflineIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M4 5a2 2 0 0 1 2-2h11v18H6a2 2 0 0 1-2-2Z" />
      <path d="M8 3v18" />
      <path d="M12 8h2" />
    </>,
  );
}

export function TrashIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M4 7h16" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M6 7l1 13h10l1-13" />
      <path d="M9 7V4h6v3" />
    </>,
  );
}

export function StarIcon({ size = 14, filled = false }: IconProps) {
  return svg(
    size,
    <path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9Z" />,
    filled,
  );
}

export function CheckIcon({ size = 14 }: IconProps) {
  return svg(size, <path d="m5 12.5 4.5 4.5L19 7.5" />);
}

export function StopIcon({ size = 14 }: IconProps) {
  return svg(size, <rect x="6" y="6" width="12" height="12" rx="1.5" />);
}

export function CloseIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M6 6l12 12" />
      <path d="M18 6 6 18" />
    </>,
  );
}

export function BackIcon({ size = 14 }: IconProps) {
  return svg(
    size,
    <>
      <path d="M19 12H5" />
      <path d="m11 6-6 6 6 6" />
    </>,
  );
}

export function ChevronIcon({ size = 12, open = false }: IconProps & { open?: boolean }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        transform: open ? 'rotate(90deg)' : 'none',
        transition: 'transform var(--dur-fast) var(--ease-standard)',
      }}
    >
      {svg(size, <path d="m9 6 6 6-6 6" />)}
    </span>
  );
}

/** One glyph per kind, so a mixed list scans by shape before by title. */
export function KindIcon({ kind, size = 14 }: { kind: string; size?: number }) {
  switch (kind) {
    case 'course':
    case 'chapter':
      return svg(
        size,
        <>
          <path d="M3 8.5 12 4l9 4.5-9 4.5Z" />
          <path d="M7 10.5V15c0 1.4 2.2 2.5 5 2.5s5-1.1 5-2.5v-4.5" />
        </>,
      );
    case 'lesson':
    case 'primer':
      return svg(
        size,
        <>
          <path d="M6 3h9l4 4v14H6Z" />
          <path d="M14 3v5h5" />
          <path d="M9 13h7" />
          <path d="M9 17h5" />
        </>,
      );
    case 'paper':
      return svg(
        size,
        <>
          <path d="M6 3h12v18H6Z" />
          <path d="M9 8h6" />
          <path d="M9 12h6" />
          <path d="M9 16h3" />
        </>,
      );
    case 'video':
      return svg(
        size,
        <>
          <rect x="3" y="6" width="13" height="12" rx="1.5" />
          <path d="m16 10 5-3v10l-5-3" />
        </>,
      );
    case 'site':
      return svg(
        size,
        <>
          <circle cx="12" cy="12" r="8.5" />
          <path d="M3.5 12h17" />
          <path d="M12 3.5c2.5 2.6 2.5 14.4 0 17" />
          <path d="M12 3.5c-2.5 2.6-2.5 14.4 0 17" />
        </>,
      );
    case 'note':
      return svg(
        size,
        <>
          <path d="M5 4h14v12l-4 4H5Z" />
          <path d="M15 20v-4h4" />
        </>,
      );
    case 'bookmark':
      return svg(size, <path d="M7 3h10v18l-5-4-5 4Z" />);
    default:
      return svg(
        size,
        <>
          <path d="M5 4h14v16H5Z" />
          <path d="M8 8h8" />
          <path d="M8 12h8" />
          <path d="M8 16h5" />
        </>,
      );
  }
}
