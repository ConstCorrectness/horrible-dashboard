import React from 'react';

export interface LocalTrackIconProps extends React.SVGProps<SVGSVGElement> {
  size?: number | string;
  className?: string;
}

/**
 * High-performance vector icon for LocalTrack experiment tracking,
 * featuring multi-run curves, gradient accents, and metric datapoints.
 */
export function LocalTrackIcon({
  size = 18,
  className = '',
  ...props
}: LocalTrackIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0 }}
      {...props}
    >
      <defs>
        <linearGradient id="ltGradientPrimary" x1="2" y1="2" x2="22" y2="22" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="50%" stopColor="#6366f1" />
          <stop offset="100%" stopColor="#a855f7" />
        </linearGradient>
        <linearGradient id="ltGradientSecondary" x1="2" y1="20" x2="22" y2="4" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#34d399" />
          <stop offset="100%" stopColor="#06b6d4" />
        </linearGradient>
      </defs>
      
      {/* Outer subtle boundary container */}
      <rect x="2" y="2" width="20" height="20" rx="5" stroke="currentColor" strokeOpacity="0.18" strokeWidth="1.25" fill="none" />
      
      {/* Base Grid line */}
      <path d="M5 19H19" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1" strokeDasharray="2 2" />
      
      {/* Primary descending metric curve (Loss curve) */}
      <path
        d="M5 6.5C7.5 7.5 9 14.5 12 15C15 15.5 16.5 16.2 19 16.8"
        stroke="url(#ltGradientPrimary)"
        strokeWidth="2"
        strokeLinecap="round"
      />

      {/* Secondary ascending metric curve (Accuracy curve) */}
      <path
        d="M5 16.5C8 16 10.5 11 13.5 9C16 7.5 17.5 7 19 6.8"
        stroke="url(#ltGradientSecondary)"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeDasharray="0.5 0"
      />

      {/* Key Datapoints */}
      <circle cx="12" cy="15" r="1.5" fill="#6366f1" />
      <circle cx="19" cy="16.8" r="1.5" fill="#a855f7" />
      <circle cx="13.5" cy="9" r="1.5" fill="#06b6d4" />
      <circle cx="19" cy="6.8" r="1.5" fill="#34d399" />
    </svg>
  );
}
