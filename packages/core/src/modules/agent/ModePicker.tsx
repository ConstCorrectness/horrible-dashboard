/**
 * The composer's mode chip — PLAN / AGENT / AUTO — and its menu. See modes.ts for
 * what each mode does; Shift+Tab in the input cycles them (ChatWidget).
 */
import { useEffect, useRef, useState } from 'react';

import { IconCheck, IconChevron } from '../../glyphs';
import { MODES, modeInfo, type AgentMode } from './modes';

export function ModePicker({
  mode,
  inherited,
  onChange,
}: {
  mode: AgentMode;
  /** The chat has no mode of its own and is showing the Settings default. */
  inherited: boolean;
  onChange: (mode: AgentMode) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('pointerdown', away);
    window.addEventListener('keydown', esc);
    return () => {
      window.removeEventListener('pointerdown', away);
      window.removeEventListener('keydown', esc);
    };
  }, [open]);

  const info = modeInfo(mode);
  return (
    <div className="agent-mode" ref={rootRef}>
      <button
        type="button"
        className="agent-mode-chip"
        data-mode={mode}
        aria-haspopup="menu"
        aria-expanded={open}
        title={`${info.label} mode${inherited ? ' (from Settings)' : ''}: ${info.hint}. Shift+Tab to switch.`}
        onClick={() => setOpen((o) => !o)}
      >
        <span>{info.label}</span>
        <IconChevron className="hd-icon agent-mode-chev" />
      </button>
      {open && (
        <ul className="agent-mode-menu" role="menu">
          {MODES.map((m) => (
            <li key={m.mode} role="none">
              <button
                type="button"
                role="menuitemradio"
                aria-checked={m.mode === mode}
                className="agent-mode-item"
                data-mode={m.mode}
                onClick={() => {
                  onChange(m.mode);
                  setOpen(false);
                }}
              >
                <span className="agent-mode-item-mark">{m.mode === mode && <IconCheck />}</span>
                <span className="agent-mode-item-text">
                  <span className="agent-mode-item-label">{m.label}</span>
                  <span className="agent-mode-item-hint">{m.hint}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
