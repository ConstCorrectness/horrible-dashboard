/**
 * What you can do to a friend, as the app's one context menu.
 *
 * Right-clicking a row and pressing its `⋯` button open the same menu, assembled
 * from these providers (see `overlay/context-menu.ts`), so the two can never offer
 * different things. Each provider reads the roster *at click time*: the menu
 * describes the friend as they are, not as they were when the row rendered.
 */
import type { ContextMenuItem, ContextMenuProvider } from '../../overlay/context-menu';
import { revealSection } from '../../layout/controller';
import { openConversation } from '../people/conversation';
import type { Friend, Persona } from './api';
import { personaLabel } from './friends-model';
import {
  blockViaChannel,
  getSocialState,
  removeViaChannel,
  respondViaChannel,
  setPersona,
} from './ws';

// ---- whose profile the Friends section is showing ------------------------------

let viewing: string | null = null;
const viewListeners = new Set<() => void>();

export function getViewingProfile(): string | null {
  return viewing;
}

export function subscribeViewingProfile(listener: () => void): () => void {
  viewListeners.add(listener);
  return () => {
    viewListeners.delete(listener);
  };
}

/** Show a profile in the Friends section, or `null` to go back to the list. */
export function viewProfile(handle: string | null): void {
  viewing = handle;
  viewListeners.forEach((l) => l());
  if (handle) revealSection('friends', 'people.home');
}

// ---- actions ---------------------------------------------------------------------

/** Open a chat in the dock beside the list. */
export function messageFriend(personId: string): void {
  viewProfile(null);
  openConversation(personId);
  revealSection('friends', 'people.home');
}

function findFriend(personId: unknown): Friend | undefined {
  return getSocialState().roster?.friends.find((f) => f.person_id === personId);
}

function friendItems(friend: Friend): ContextMenuItem[][] {
  const id = friend.person_id;
  if (friend.status === 'pending_in') {
    return [
      [
        { id: 'accept', label: 'Accept friend request', run: () => respondViaChannel(id, true) },
        { id: 'decline', label: 'Decline', run: () => respondViaChannel(id, false) },
      ],
      [{ id: 'block', label: 'Block', danger: true, run: () => blockViaChannel(id) }],
    ];
  }
  if (friend.status === 'pending_out') {
    return [[{ id: 'cancel', label: 'Cancel request', run: () => removeViaChannel(id) }]];
  }

  const primary: ContextMenuItem[] = [
    { id: 'message', label: 'Send message', run: () => messageFriend(id) },
  ];
  if (friend.handle) {
    const handle = friend.handle;
    primary.push({
      id: 'profile',
      label: 'View profile',
      hint: `@${handle}`,
      run: () => viewProfile(handle),
    });
  }
  // Their machines, so "my other computer" stays findable — the one place a node
  // label is still a useful thing to show. Informational, so every row is inert.
  if (friend.devices.length > 0) {
    primary.push({
      id: 'machines',
      label: 'Machines',
      run: () => {},
      submenu: friend.devices.map((d) => ({
        id: d.node_id,
        label: d.label,
        hint: d.online ? 'online' : 'offline',
        disabled: true,
        run: () => {},
      })),
    });
  }

  const manage: ContextMenuItem[] = [
    { id: 'remove', label: 'Remove friend', danger: true, run: () => removeViaChannel(id) },
  ];
  // Blocking yourself is meaningless; removing one of your own machines is not.
  if (!friend.is_self) {
    manage.push({ id: 'block', label: 'Block', danger: true, run: () => blockViaChannel(id) });
  }
  return [primary, manage];
}

const PERSONAS: Persona[] = ['online', 'away', 'busy', 'invisible'];

const PERSONA_DETAIL: Record<Persona, string> = {
  online: 'Friends see you as available.',
  away: 'Online, but not at the keyboard.',
  busy: 'Online, and asking not to be bothered.',
  invisible: 'Appear offline. You can still chat.',
};

/**
 * Providers are per *group*: the context menu draws a separator between providers
 * and none inside one, so a friend's manage actions (remove, block) are a second
 * provider rather than a second array — the separator is what keeps "Block" from
 * sitting flush under "Send message".
 */
export const socialContextMenu: ContextMenuProvider[] = [
  {
    kind: 'social.friend',
    order: 0,
    items: (target) => {
      const friend = findFriend(target.personId);
      return friend ? (friendItems(friend)[0] ?? []) : [];
    },
  },
  {
    kind: 'social.friend',
    order: 10,
    items: (target) => {
      const friend = findFriend(target.personId);
      return friend ? (friendItems(friend)[1] ?? []) : [];
    },
  },
  {
    kind: 'social.self',
    order: 0,
    items: () => {
      const current = getSocialState().roster?.self_profile.persona ?? 'online';
      return PERSONAS.map((p) => ({
        id: p,
        label: personaLabel(p),
        detail: PERSONA_DETAIL[p],
        checked: p === current,
        run: () => setPersona(p),
      }));
    },
  },
  {
    kind: 'social.self',
    order: 10,
    items: () => {
      const handle = getSocialState().roster?.self_profile.handle;
      if (!handle) return [];
      return [
        {
          id: 'copy',
          label: 'Copy username',
          hint: `@${handle}`,
          run: () => void navigator.clipboard?.writeText(`@${handle}`),
        },
        { id: 'profile', label: 'View my profile', run: () => viewProfile(handle) },
      ];
    },
  },
];
