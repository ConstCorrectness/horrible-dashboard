/**
 * Service contracts: the typed seam between modules.
 *
 * A module never imports another module's files. When it needs something
 * another module owns, it names a contract here, the owner `provide`s it, and
 * the user `getService`s it — tolerating `undefined`, because the provider may
 * be disabled, uninstalled, or running in another process.
 *
 * A contract id carries a major version (`@1`). A breaking change is a new key,
 * so an old provider and a new consumer fail loudly at the type level instead
 * of quietly at runtime.
 */

/** A fader on the audio mixer: one source of sound a module owns. */
export interface MixerStripDecl {
  /** Stable id. Persisted in the routing matrix, so renaming one loses its routing. */
  id: string;
  /** Shown on the fader. */
  label: string;
  /** Glyph shown in the mixer, matching the module's own rail icon. */
  icon?: string;
}

export interface ServiceContracts {
  /**
   * Route an `<audio>`/`<video>` element's sound through the mixer as `strip`, for
   * as long as the calling component lives. A hook: call it unconditionally.
   * Pass the element (held via a callback ref), not a `RefObject`.
   */
  'audio.mediaStrip@1': {
    useMediaStrip(el: HTMLMediaElement | null, strip: MixerStripDecl): void;
  };
}

export type ServiceKey = keyof ServiceContracts;
