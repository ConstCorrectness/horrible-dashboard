export { AppRoot } from './AppRoot';
export { AppShell } from './AppShell';
// Avatar3D moved to @horrible/core (so core feature modules — e.g. the agent chat
// widget — can use it without a core→ui cycle). Re-exported here for back-compat.
export {
  Avatar3D,
  DEFAULT_AVATAR_MOOD,
  DEFAULT_AVATAR_MOODS,
  type AvatarMoodMap,
} from '@horrible/core';
export { Spotlight } from './Spotlight';
export { HomeView } from './HomeView';
export { Frame } from './layout/Frame';
export { Toasts } from './Toasts';
