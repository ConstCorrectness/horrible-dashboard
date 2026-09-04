import React from 'react';

export interface IconProps {
  size?: number;
  color?: string;
  className?: string;
  style?: React.CSSProperties;
}

export const IconMesh: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <path d="M8 1.5L14.5 5.2V10.8L8 14.5L1.5 10.8V5.2L8 1.5Z" />
    <path d="M8 1.5V14.5" />
    <path d="M1.5 5.2L14.5 10.8" />
    <path d="M14.5 5.2L1.5 10.8" />
  </svg>
);

export const IconArmature: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <circle cx="8" cy="3" r="2" />
    <circle cx="4" cy="13" r="2" />
    <circle cx="12" cy="13" r="2" />
    <path d="M7 4.7L4.7 11.3" />
    <path d="M9 4.7L11.3 11.3" />
  </svg>
);

export const IconCrosshair: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <circle cx="8" cy="8" r="5.5" />
    <circle cx="8" cy="8" r="1.5" />
    <path d="M8 1V4" />
    <path d="M8 12V15" />
    <path d="M1 8H4" />
    <path d="M12 8H15" />
  </svg>
);

export const IconActor: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <circle cx="8" cy="4.5" r="2.5" />
    <path d="M3 14C3 11.2 5.2 9 8 9C10.8 9 13 11.2 13 14" />
  </svg>
);

export const IconFilm: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <rect x="2" y="2.5" width="12" height="11" rx="1.5" />
    <path d="M2 6H14" />
    <path d="M2 10H14" />
    <path d="M5.5 2.5V6" />
    <path d="M10.5 2.5V6" />
    <path d="M5.5 10V13.5" />
    <path d="M10.5 10V13.5" />
  </svg>
);

export const IconTexture: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <circle cx="8" cy="8" r="6" />
    <path d="M2 8C5 8 6.5 4 8 2" />
    <path d="M8 14C9.5 12 11 8 14 8" />
    <path d="M3.5 4C6 5.5 10 5.5 12.5 4" />
    <path d="M3.5 12C6 10.5 10 10.5 12.5 12" />
  </svg>
);

export const IconMap: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <polygon points="1.5,4 5.5,2 10.5,4 14.5,2 14.5,12 10.5,14 5.5,12 1.5,14" />
    <line x1="5.5" y1="2" x2="5.5" y2="12" />
    <line x1="10.5" y1="4" x2="10.5" y2="14" />
  </svg>
);

export const IconTransform: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <line x1="3" y1="13" x2="13" y2="13" />
    <line x1="3" y1="13" x2="3" y2="3" />
    <line x1="3" y1="13" x2="8" y2="8" />
    <polyline points="11,11 13,13 11,15" />
    <polyline points="1,5 3,3 5,5" />
    <polyline points="6,7 8,8 7,9" />
  </svg>
);

export const IconLayers: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <polygon points="8,1.5 14,4.5 8,7.5 2,4.5" />
    <polyline points="2,8 8,11 14,8" />
    <polyline points="2,11.5 8,14.5 14,11.5" />
  </svg>
);

export const IconEye: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <path d="M1.5 8C2.5 5 5 3 8 3C11 3 13.5 5 14.5 8C13.5 11 11 13 8 13C5 13 2.5 11 1.5 8Z" />
    <circle cx="8" cy="8" r="2" />
  </svg>
);

export const IconEyeOff: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <path d="M2 2L14 14" />
    <path d="M6.5 6.7C6.9 6.2 7.4 6 8 6C9.1 6 10 6.9 10 8C10 8.6 9.8 9.1 9.3 9.5" />
    <path d="M4.2 4.5C2.8 5.4 1.9 6.6 1.5 8C2.5 11 5 13 8 13C9.3 13 10.5 12.6 11.5 11.8" />
    <path d="M8 3C11 3 13.5 5 14.5 8C14.1 9.1 13.5 10 12.6 10.7" />
  </svg>
);

export const IconLock: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <rect x="3.5" y="7" width="9" height="7" rx="1.5" />
    <path d="M5.5 7V4.5C5.5 3.1 6.6 2 8 2C9.4 2 10.5 3.1 10.5 4.5V7" />
  </svg>
);

export const IconSun: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <circle cx="8" cy="8" r="3" />
    <path d="M8 1.5V3" />
    <path d="M8 13V14.5" />
    <path d="M1.5 8H3" />
    <path d="M13 8H14.5" />
    <path d="M3.4 3.4L4.5 4.5" />
    <path d="M11.5 11.5L12.6 12.6" />
    <path d="M3.4 12.6L4.5 11.5" />
    <path d="M11.5 4.5L12.6 3.4" />
  </svg>
);

export const IconGrid: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <rect x="2" y="2" width="12" height="12" rx="1" />
    <line x1="2" y1="6" x2="14" y2="6" />
    <line x1="2" y1="10" x2="14" y2="10" />
    <line x1="6" y1="2" x2="6" y2="14" />
    <line x1="10" y1="2" x2="10" y2="14" />
  </svg>
);

export const IconBone: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <polygon points="8,2 10.5,7 9,14 7,14 5.5,7" />
    <circle cx="8" cy="2.5" r="1.25" />
  </svg>
);

export const IconSearch: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <circle cx="7" cy="7" r="4.5" />
    <line x1="10.5" y1="10.5" x2="14" y2="14" />
  </svg>
);

export const IconClose: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <line x1="3.5" y1="3.5" x2="12.5" y2="12.5" />
    <line x1="12.5" y1="3.5" x2="3.5" y2="12.5" />
  </svg>
);

export const IconChevronDown: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <polyline points="4,6 8,10 12,6" />
  </svg>
);

export const IconChevronRight: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <polyline points="6,4 10,8 6,12" />
  </svg>
);

export const IconPlus: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <line x1="8" y1="3" x2="8" y2="13" />
    <line x1="3" y1="8" x2="13" y2="8" />
  </svg>
);

export const IconTrash: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <polyline points="2.5,4 13.5,4" />
    <path d="M5.5 4V2.5C5.5 2 6 1.5 6.5 1.5H9.5C10 1.5 10.5 2 10.5 2.5V4" />
    <path d="M4 4L4.8 13.2C4.9 14 5.5 14.5 6.3 14.5H9.7C10.5 14.5 11.1 14 11.2 13.2L12 4" />
  </svg>
);

export const IconPlay: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill={color} stroke="none" style={style}>
    <polygon points="4,3 13,8 4,13" />
  </svg>
);

export const IconPause: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill={color} stroke="none" style={style}>
    <rect x="3.5" y="3" width="3.5" height="10" rx="1" />
    <rect x="9" y="3" width="3.5" height="10" rx="1" />
  </svg>
);

export const IconLoop: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <path d="M12.5 6.5A5 5 0 0 0 4 4.5L2 6.5" />
    <polyline points="2,3 2,6.5 5.5,6.5" />
    <path d="M3.5 9.5A5 5 0 0 0 12 11.5L14 9.5" />
    <polyline points="14,13 14,9.5 10.5,9.5" />
  </svg>
);

export const IconDownload: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <path d="M8 2V10" />
    <polyline points="5,7.5 8,10.5 11,7.5" />
    <path d="M2.5 12.5V13.5C2.5 14 3 14.5 3.5 14.5H12.5C13 14.5 13.5 14 13.5 13.5V12.5" />
  </svg>
);

export const IconUpload: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <path d="M8 10V2" />
    <polyline points="5,4.5 8,1.5 11,4.5" />
    <path d="M2.5 12.5V13.5C2.5 14 3 14.5 3.5 14.5H12.5C13 14.5 13.5 14 13.5 13.5V12.5" />
  </svg>
);

export const IconWrench: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <path d="M14 4.5A3.5 3.5 0 0 1 10 7.8L5.5 12.3C4.8 13 3.7 13 3 12.3L2.7 12C2 11.3 2 10.2 2.7 9.5L7.2 5A3.5 3.5 0 0 1 10.5 1L9 3.5L11.5 6L14 4.5Z" />
  </svg>
);

export const IconSidebarLeft: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <rect x="2" y="2.5" width="12" height="11" rx="1.5" />
    <line x1="6" y1="2.5" x2="6" y2="13.5" />
    <polyline points="4.5,8 3.5,8" />
  </svg>
);

export const IconSpawn: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <circle cx="8" cy="8" r="5.5" />
    <polygon points="8,4.5 10.5,10.5 8,9 5.5,10.5" fill={color} />
  </svg>
);

export const IconPickup: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <polygon points="8,1.5 14,5 14,11 8,14.5 2,11 2,5" />
    <polyline points="8,7 8,14" />
    <polyline points="2,5 8,7 14,5" />
  </svg>
);

export const IconCopy: React.FC<IconProps> = ({ size = 14, color = 'currentColor', style }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={style}>
    <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
    <path d="M3.5 10.5H2.5C2 10.5 1.5 10 1.5 9.5V2.5C1.5 2 2 1.5 2.5 1.5H9.5C10 1.5 10.5 2 10.5 2.5V3.5" />
  </svg>
);
