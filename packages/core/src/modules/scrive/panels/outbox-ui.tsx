/**
 * What the Share and Outbox panes both draw: a target's name and mark, and times.
 * The marks are monochrome and inherit `currentColor` (the in-pane icon rule), so a
 * row's status colour carries through them.
 */
import type { ReactNode } from 'react';

import type { OutboxTarget } from '../api';

export const TARGET_LABEL: Record<OutboxTarget, string> = {
  x: 'X',
  linkedin: 'LinkedIn',
  youtube: 'YouTube',
  devto: 'dev.to',
  hashnode: 'Hashnode',
};

const MARKS: Record<OutboxTarget, ReactNode> = {
  x: <path d="M4 4l16 16M20 4L4 20" />,
  linkedin: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M8 10v7M8 7v.01M12 17v-4a2 2 0 0 1 4 0v4M12 10v7" />
    </>
  ),
  youtube: (
    <>
      <rect x="2.5" y="5" width="19" height="14" rx="4" />
      <path d="M10 9.5v5l4.5-2.5z" />
    </>
  ),
  // Line drawings of the two marks: DEV's letters in a box, Hashnode's ring.
  devto: (
    <>
      <rect x="2.5" y="5" width="19" height="14" rx="2" />
      <path d="M6.5 9v6h1.2a1.8 1.8 0 0 0 1.8-1.8v-2.4A1.8 1.8 0 0 0 7.7 9zM13.5 9h-2v6h2M11.5 12h1.5M15.5 9l1.5 6 1.5-6" />
    </>
  ),
  hashnode: (
    <>
      <path d="M12 2.5l9.5 9.5-9.5 9.5L2.5 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
};

export function TargetIcon({ target, size = 13 }: { target: OutboxTarget; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-label={TARGET_LABEL[target]}
      role="img"
      style={{ flexShrink: 0 }}
    >
      {MARKS[target]}
    </svg>
  );
}

/** Epoch seconds → a short local date and time. */
export function when(seconds: number): string {
  const d = new Date(seconds * 1000);
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
}
