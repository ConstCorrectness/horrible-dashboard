/**
 * HorribleAssault's front door: the loading build, the sign-in, the username, and
 * the deploy prompt — one full-pane layer over the live scene.
 *
 * It is a **DOM overlay on the game's own canvas**, not a separate view. The map
 * assembling behind it and the slow orbit that follows are the same three.js scene
 * you play in (see reveal.ts / backdrop.ts), which is why signing in happens over
 * a real level rather than a screenshot or a spinner.
 *
 * The loading numbers are real: the percentage is weighted actual work and the
 * byte counter is the map download's own `Content-Length` (see boot.ts). Nothing
 * here is a fake progress bar waiting out a timer.
 */
import { useState, type CSSProperties, type ReactNode } from 'react';

import { setUsername } from '../../account';
import { SignInCard } from '../../SignInCard';
import type { SessionInfo } from './api';
import { bootProgress, formatBytes, statusLine, type BootPhase, type BootProgress } from './boot';

/** Scoped so the pane can style pseudo-classes and keyframes, which inline styles
 * can't reach. Reduced motion kills the pulse rather than the information. */
const STYLES = `
@keyframes hd-boot-pulse { 0%,100% { opacity: .45 } 50% { opacity: .9 } }
@keyframes hd-boot-in { from { opacity: 0; transform: translateY(6px) } to { opacity: 1; transform: none } }
.hd-boot { animation: hd-boot-in .5s ease both; }
.hd-boot-status { animation: hd-boot-pulse 2.4s ease-in-out infinite; }
.hd-boot input:focus-visible, .hd-boot button:focus-visible {
  outline: 2px solid var(--accent, #6ea8fe); outline-offset: 2px;
}
.hd-boot-btn:hover:not(:disabled) { background: rgba(150,160,190,.14); border-color: rgba(150,160,190,.42); }
.hd-boot-btn:disabled { opacity: .5; cursor: not-allowed; }
.hd-boot-primary:hover:not(:disabled) { filter: brightness(1.12); }
.hd-boot-link { background: none; border: 0; padding: 0; cursor: pointer; text-decoration: underline; }
@media (prefers-reduced-motion: reduce) {
  .hd-boot, .hd-boot-status { animation: none !important; }
}
`;

const MONO = 'var(--font-mono, "JetBrains Mono", Consolas, monospace)';
const HAIR = 'rgba(150,160,190,.22)';
const PANEL = 'rgba(150,160,190,.06)';

const shell: CSSProperties = {
  position: 'absolute',
  inset: 0,
  zIndex: 10, // above the invite toasts (zIndex 2)
  pointerEvents: 'auto', // it has real inputs; the click-to-play layer does not
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  padding: '1.5rem',
  // Light on purpose. The map orbiting behind this is the point of rendering the
  // sign-in over a live scene at all, and a scrim heavy enough to guarantee
  // contrast everywhere would throw that away — the card below buys its own
  // legibility instead.
  background:
    'radial-gradient(120% 90% at 50% 55%, rgba(5,6,9,.55) 0%, rgba(5,6,9,.30) 60%, rgba(5,6,9,.62) 100%)',
  color: 'var(--text, #e8eaf2)',
  overflowY: 'auto',
};

const column: CSSProperties = {
  width: '100%',
  maxWidth: 340,
  display: 'flex',
  flexDirection: 'column',
  gap: '0.85rem',
  // A card rather than bare text on the scene: the blur keeps every label
  // readable over whatever happens to be behind it, without dimming the map.
  padding: '1.25rem',
  borderRadius: 8,
  border: `1px solid ${HAIR}`,
  background: 'rgba(5,6,9,.62)',
  backdropFilter: 'blur(10px) saturate(1.1)',
  WebkitBackdropFilter: 'blur(10px) saturate(1.1)',
  boxShadow: '0 18px 50px rgba(0,0,0,.45)',
};

const label: CSSProperties = {
  fontFamily: MONO,
  fontSize: '0.62rem',
  letterSpacing: '0.16em',
  textTransform: 'uppercase',
  color: 'var(--text-dim, #8a909c)',
};

const field: CSSProperties = {
  width: '100%',
  padding: '0.5rem 0.6rem',
  background: 'rgba(5,6,9,.55)',
  border: `1px solid ${HAIR}`,
  borderRadius: 4,
  color: 'var(--text, #e8eaf2)',
  fontSize: '0.85rem',
  fontFamily: 'inherit',
};

const button: CSSProperties = {
  width: '100%',
  padding: '0.55rem 0.7rem',
  background: PANEL,
  border: `1px solid ${HAIR}`,
  borderRadius: 4,
  color: 'var(--text, #e8eaf2)',
  fontSize: '0.82rem',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '0.5rem',
  transition: 'background .15s, border-color .15s',
};

const primary: CSSProperties = {
  ...button,
  background: 'var(--accent, #6ea8fe)',
  // The shorthand, not `borderColor`: `button` above sets `border`, and mixing a
  // shorthand with its own longhand in one computed style is what React warns about.
  border: '1px solid transparent',
  color: '#08111f',
  fontWeight: 600,
};

function Wordmark({ sub }: { sub: string }) {
  return (
    <div style={{ textAlign: 'center', marginBottom: '0.4rem' }}>
      <div
        style={{
          fontSize: '1.15rem',
          fontWeight: 700,
          letterSpacing: '0.22em',
          textTransform: 'uppercase',
        }}
      >
        Horrible<span style={{ color: 'var(--accent, #6ea8fe)' }}>Assault</span>
      </div>
      <div style={{ ...label, marginTop: '0.35rem' }}>{sub}</div>
    </div>
  );
}

function Err({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <div
      role="alert"
      style={{ color: '#ff9d94', fontSize: '0.76rem', lineHeight: 1.45, textAlign: 'center' }}
    >
      {children}
    </div>
  );
}

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e));

// ---- loading -----------------------------------------------------------------

function Loading({
  progress,
  bytes,
  mapName,
  error,
}: {
  progress: BootProgress;
  bytes: { loaded: number; total: number | null };
  mapName: string;
  error: string | null;
}) {
  const pct = Math.round(bootProgress(progress) * 100);
  return (
    <div className="hd-boot" style={column}>
      <Wordmark sub={mapName || 'standing by'} />
      <div
        style={{
          fontFamily: MONO,
          fontSize: '2.1rem',
          fontWeight: 300,
          textAlign: 'center',
          fontVariantNumeric: 'tabular-nums',
          letterSpacing: '-0.02em',
        }}
      >
        {pct}
        <span style={{ fontSize: '1rem', color: 'var(--text-dim, #8a909c)' }}>%</span>
      </div>
      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Loading HorribleAssault"
        style={{ height: 2, background: HAIR, borderRadius: 1, overflow: 'hidden' }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: '100%',
            background: 'var(--accent, #6ea8fe)',
            // Eased so a cached map (which completes in one frame) still reads as
            // a load rather than a jump.
            transition: 'width .35s cubic-bezier(.2,.7,.3,1)',
          }}
        />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem' }}>
        <span
          className={error ? undefined : 'hd-boot-status'}
          style={{ ...label, color: error ? '#ff9d94' : undefined }}
        >
          {statusLine(progress, error)}
        </span>
        <span style={{ ...label, fontVariantNumeric: 'tabular-nums' }}>
          {formatBytes(bytes.loaded, bytes.total)}
        </span>
      </div>
    </div>
  );
}

// ---- sign in / sign up -------------------------------------------------------

/**
 * The front door's sign-in is now the app's one sign-in (`SignInCard` in core).
 *
 * This used to be its own ~200-line copy of the OAuth dance, and one of three in
 * the repo. They had already drifted apart — this one had the blocked-popup
 * fallback, the games first-run hero did not — which is exactly how a bug ends up
 * living in one screen and not its twin. All that belongs here is the frame: the
 * wordmark, and the fact that signing in happens over a real level.
 */
function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  return (
    <div className="hd-boot" style={column}>
      <Wordmark sub="sign in to play" />
      <SignInCard onSignedIn={onSignedIn} />
    </div>
  );
}

// ---- enlist (claim a username) -----------------------------------------------

function Enlist({ account, onEnlisted }: { account: SessionInfo | null; onEnlisted: () => void }) {
  // Pre-filled with the server's suggestion (the provider login, folded into the
  // handle charset) rather than blank. The suggestion is the *only* thing left of
  // what sign-in used to do silently: it derived this name and claimed it, so the
  // person never saw this screen. Now they see it, and the name is theirs to keep
  // or replace before anything is reserved.
  const [value, setValue] = useState(account?.suggested_username ?? '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setErr('');
    try {
      await setUsername(value.trim());
      onEnlisted();
    } catch (e) {
      // The server owns uniqueness and the charset rule, so its message is shown
      // verbatim rather than second-guessed here.
      setErr(message(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="hd-boot" style={column}>
      <Wordmark sub="choose your username" />
      <p
        style={{
          ...label,
          textTransform: 'none',
          letterSpacing: 0,
          fontFamily: 'inherit',
          fontSize: '0.78rem',
          lineHeight: 1.5,
          textAlign: 'center',
          margin: 0,
        }}
      >
        Signed in as {account?.display_name ?? 'you'}. Your username is how everyone else sees you —
        on the scoreboard, in the killfeed, on the ladder, and it is how friends add you.
      </p>
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        <input
          style={{ ...field, fontFamily: MONO, letterSpacing: '0.05em' }}
          placeholder="username"
          aria-label="Username"
          required
          minLength={3}
          maxLength={20}
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <span style={label}>3–20 characters · a–z 0–9 - _</span>
        <button
          className="hd-boot-primary"
          style={primary}
          type="submit"
          disabled={busy || value.trim().length < 3}
        >
          {busy ? 'Claiming…' : 'Enlist'}
        </button>
      </form>
      <Err>{err}</Err>
    </div>
  );
}

// ---- the overlay -------------------------------------------------------------

export interface BootOverlayProps {
  phase: BootPhase;
  progress: BootProgress;
  bytes: { loaded: number; total: number | null };
  mapName: string;
  error: string | null;
  account: SessionInfo | null;
  /** Re-read the account from the backend (and the game server). */
  onSignedIn: () => void;
  /**
   * The main menu, for the `menu` phase.
   *
   * Passed in as a slot rather than built here. It needs the map list, the roster,
   * the session and half a dozen callbacks, and threading all of that through this
   * component — whose job is the scrim, the loading bar and the sign-in form —
   * would make it the panel's second constructor. What it contributes instead is
   * exactly what the menu should share with sign-in: the same layer over the same
   * live scene.
   */
  menu?: ReactNode;
}

export function BootOverlay({
  phase,
  progress,
  bytes,
  mapName,
  error,
  account,
  onSignedIn,
  menu,
}: BootOverlayProps) {
  return (
    <div style={shell}>
      <style>{STYLES}</style>
      {phase === 'loading' && (
        <Loading progress={progress} bytes={bytes} mapName={mapName} error={error} />
      )}
      {phase === 'signin' && <SignIn onSignedIn={onSignedIn} />}
      {phase === 'enlist' && <Enlist account={account} onEnlisted={onSignedIn} />}
      {phase === 'menu' && menu}
    </div>
  );
}
