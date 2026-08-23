/**
 * Developer Console UI for hAssault.
 *
 * Provides a Counter-Strike / Source-engine style developer console with
 * interactive CVar manipulation, concommand dispatching, Python macro execution,
 * IntelliSense autocomplete, and quick developer toggles.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { consoleExecutor } from './executor';
import { consoleRegistry } from './registry';
import { fetchMacros } from './macros';
import type { AutocompleteItem, ConsoleLogEntry, MacroRecord } from './types';

export interface DeveloperConsoleProps {
  isOpen?: boolean;
  onClose?: () => void;
  isStandalone?: boolean;
  roomId?: string;
  mapName?: string;
  rttMs?: number;
}

export function DeveloperConsole({
  isOpen = true,
  onClose,
  isStandalone,
  roomId = '',
  mapName = '',
  rttMs = 0,
}: DeveloperConsoleProps) {
  const standalone = isStandalone ?? !onClose;
  const [logs, setLogs] = useState<ConsoleLogEntry[]>([
    {
      id: 'init-1',
      timestamp: Date.now(),
      level: 'system',
      text: 'hAssault Developer Console initialized. Type "help" for a list of CVars and commands.',
    },
  ]);
  const [inputVal, setInputVal] = useState('');
  const [multiLine, setMultiLine] = useState(false);
  const [filter, setFilter] = useState<'all' | 'net' | 'draw' | 'server' | 'macro'>('all');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [macros, setMacros] = useState<MacroRecord[]>([]);
  const [selectedMacro, setSelectedMacro] = useState<MacroRecord | null>(null);

  // Autocomplete state
  const [suggestions, setSuggestions] = useState<AutocompleteItem[]>([]);
  const [selectedSugIdx, setSelectedSugIdx] = useState(0);

  // Quick toggle mirrors
  const [hitboxes, setHitboxes] = useState<boolean>(consoleRegistry.get('draw.hitboxes') ?? false);
  const [wireframe, setWireframe] = useState<boolean>(consoleRegistry.get('draw.wireframe') ?? false);
  const [netGraph, setNetGraph] = useState<number>(consoleRegistry.get('net.graph') ?? 0);
  const [cheats, setCheats] = useState<boolean>(consoleRegistry.get('server.cheats') ?? false);
  const [timescale, setTimescale] = useState<number>(consoleRegistry.get('server.timescale') ?? 1.0);
  const [godMode, setGodMode] = useState<boolean>(consoleRegistry.get('player.god') ?? false);

  const logEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);

  // Sync definitions and macros on mount
  useEffect(() => {
    void consoleRegistry.syncDefinitions();
    void fetchMacros().then(setMacros);

    return consoleRegistry.subscribe((name, val) => {
      if (name === 'draw.hitboxes') setHitboxes(Boolean(val));
      if (name === 'draw.wireframe') setWireframe(Boolean(val));
      if (name === 'net.graph') setNetGraph(Number(val) || 0);
      if (name === 'server.cheats') setCheats(Boolean(val));
      if (name === 'server.timescale') setTimescale(Number(val) || 1.0);
      if (name === 'player.god') setGodMode(Boolean(val));
    });
  }, []);

  // Auto-scroll logs
  useEffect(() => {
    if (isOpen) {
      logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, isOpen]);

  // Focus input when opened
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  const appendLog = useCallback((level: ConsoleLogEntry['level'], text: string) => {
    setLogs((prev) => [
      ...prev,
      {
        id: `log-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        timestamp: Date.now(),
        level,
        text,
      },
    ]);
  }, []);

  const runCommand = useCallback(
    async (cmd: string) => {
      const trimmed = cmd.trim();
      if (!trimmed) return;

      appendLog('input', `> ${trimmed}`);

      if (trimmed.toLowerCase() === 'clear') {
        setLogs([]);
        return;
      }

      const res = await consoleExecutor.execute(trimmed, { room: roomId });
      if (res.output && res.output.length > 0) {
        for (const line of res.output) {
          appendLog('output', line);
        }
      }
      if (!res.ok && res.error) {
        appendLog('error', res.error);
      }
    },
    [appendLog, roomId],
  );

  const handleInputChange = (val: string) => {
    setInputVal(val);
    if (!val.trim() || val.includes('\n')) {
      setSuggestions([]);
      return;
    }
    const token = val.split(/\s+/)[0];
    const matches = consoleRegistry.autocomplete(token, 8);
    setSuggestions(matches);
    setSelectedSugIdx(0);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Autocomplete navigation
    if (suggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedSugIdx((prev) => (prev + 1) % suggestions.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedSugIdx((prev) => (prev - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && suggestions.length > 0)) {
        e.preventDefault();
        const picked = suggestions[selectedSugIdx];
        if (picked) {
          setInputVal(picked.name + (picked.kind === 'cvar' ? ' ' : ''));
          setSuggestions([]);
          return;
        }
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const cmd = inputVal;
      setInputVal('');
      setSuggestions([]);
      void runCommand(cmd);
      return;
    }

    // History navigation (Up/Down) when suggestions are closed
    if (suggestions.length === 0 && !multiLine) {
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        const prev = consoleExecutor.historyPrev();
        if (prev != null) setInputVal(prev);
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        const next = consoleExecutor.historyNext();
        if (next != null) setInputVal(next);
        return;
      }
    }

    // Close on Backquote or Escape in overlay mode
    if (!standalone && (e.code === 'Backquote' || e.key === 'Escape')) {
      e.preventDefault();
      onClose?.();
    }
  };

  const copyLogs = () => {
    const text = logs.map((l) => `[${new Date(l.timestamp).toLocaleTimeString()}] ${l.text}`).join('\n');
    navigator.clipboard.writeText(text);
    appendLog('system', 'Console logs copied to clipboard.');
  };

  if (!isOpen) return null;

  const filteredLogs = logs.filter((l) => {
    if (filter === 'all') return true;
    if (filter === 'net') return l.text.toLowerCase().includes('net.') || l.text.toLowerCase().includes('ping');
    if (filter === 'draw') return l.text.toLowerCase().includes('draw.') || l.text.toLowerCase().includes('hitbox');
    if (filter === 'server') return l.text.toLowerCase().includes('server.') || l.text.toLowerCase().includes('bot');
    if (filter === 'macro') return l.text.toLowerCase().includes('macro.') || l.text.toLowerCase().includes('[macro]');
    return true;
  });

  return (
    <div
      style={{
        ...(standalone ? styles.standaloneContainer : styles.overlayContainer),
      }}
      onClick={(e) => e.stopPropagation()}
    >
      {/* Console Window */}
      <div style={styles.window}>
        {/* Top Header Bar */}
        <div style={styles.header}>
          <div style={styles.headerLeft}>
            <span style={styles.titleBadge}>~ DEV CONSOLE</span>
            <span style={styles.statusChip}>
              {roomId ? `ROOM: ${roomId} (${mapName || 'map'})` : 'LOCAL DEV'}
            </span>
            <span style={styles.statusChip}>PING: {rttMs}ms</span>
            <span
              style={{
                ...styles.statusChip,
                color: cheats ? '#facc15' : '#94a3b8',
                borderColor: cheats ? '#facc15' : '#334155',
              }}
            >
              CHEATS: {cheats ? 'ON' : 'OFF'}
            </span>
          </div>

          <div style={styles.headerRight}>
            <div style={styles.filterGroup}>
              {(['all', 'net', 'draw', 'server', 'macro'] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  style={{
                    ...styles.filterBtn,
                    ...(filter === f ? styles.filterBtnActive : null),
                  }}
                >
                  {f.toUpperCase()}
                </button>
              ))}
            </div>

            <button
              onClick={() => setDrawerOpen(!drawerOpen)}
              style={{
                ...styles.actionBtn,
                ...(drawerOpen ? styles.actionBtnActive : null),
              }}
              title="Toggle Macros Library"
            >
              📜 Macros
            </button>

            <button onClick={copyLogs} style={styles.actionBtn} title="Copy Logs">
              📋 Copy
            </button>

            <button onClick={() => setLogs([])} style={styles.actionBtn} title="Clear Log Output">
              🗑️ Clear
            </button>

            {!standalone && (
              <button onClick={onClose} style={styles.closeBtn} title="Close Console (` or Esc)">
                ✕
              </button>
            )}
          </div>
        </div>

        {/* Quick Developer Action Toolbar */}
        <div style={styles.quickBar}>
          <button
            onClick={() => void runCommand(`draw.hitboxes ${hitboxes ? 0 : 1}`)}
            style={{
              ...styles.quickBtn,
              ...(hitboxes ? styles.quickBtnActive : null),
            }}
          >
            🎯 Hitboxes: {hitboxes ? 'ON' : 'OFF'}
          </button>

          <button
            onClick={() => void runCommand(`draw.wireframe ${wireframe ? 0 : 1}`)}
            style={{
              ...styles.quickBtn,
              ...(wireframe ? styles.quickBtnActive : null),
            }}
          >
            🕸️ Wireframe: {wireframe ? 'ON' : 'OFF'}
          </button>

          <button
            onClick={() => void runCommand(`net.graph ${netGraph > 0 ? 0 : 1}`)}
            style={{
              ...styles.quickBtn,
              ...(netGraph > 0 ? styles.quickBtnActive : null),
            }}
          >
            📊 NetGraph: {netGraph > 0 ? `L${netGraph}` : 'OFF'}
          </button>

          <button
            onClick={() => void runCommand(`player.god ${godMode ? 0 : 1}`)}
            style={{
              ...styles.quickBtn,
              ...(godMode ? styles.quickBtnActive : null),
            }}
          >
            🛡️ God: {godMode ? 'ON' : 'OFF'}
          </button>

          <button
            onClick={() => void runCommand('server.bots.add(count=1, skill="normal")')}
            style={styles.quickBtn}
          >
            🤖 +1 Bot
          </button>

          <button
            onClick={() => void runCommand('server.bots.kick_all()')}
            style={styles.quickBtn}
          >
            🚫 Kick Bots
          </button>

          <div style={styles.timescaleContainer}>
            <span style={{ fontSize: '0.7rem', color: '#94a3b8' }}>Speed:</span>
            <button
              onClick={() => void runCommand('server.timescale 0.35')}
              style={{ ...styles.quickBtnSmall, ...(timescale === 0.35 ? styles.quickBtnActive : null) }}
            >
              0.35x
            </button>
            <button
              onClick={() => void runCommand('server.timescale 1.0')}
              style={{ ...styles.quickBtnSmall, ...(timescale === 1.0 ? styles.quickBtnActive : null) }}
            >
              1.0x
            </button>
            <button
              onClick={() => void runCommand('server.timescale 2.0')}
              style={{ ...styles.quickBtnSmall, ...(timescale === 2.0 ? styles.quickBtnActive : null) }}
            >
              2.0x
            </button>
          </div>

          <button
            onClick={() => void runCommand('macro.run("warmup")')}
            style={{ ...styles.quickBtn, marginLeft: 'auto', background: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8' }}
          >
            ⚡ Warmup Drill
          </button>
        </div>

        {/* Console Body Area: Log Output + Side Macro Drawer */}
        <div style={styles.bodyWrapper}>
          {/* Main Terminal Output */}
          <div style={styles.logOutput}>
            {filteredLogs.map((item) => {
              let color = '#e2e8f0';
              if (item.level === 'input') color = '#38bdf8';
              else if (item.level === 'error') color = '#f87171';
              else if (item.level === 'warning') color = '#facc15';
              else if (item.level === 'system') color = '#a855f7';
              else if (item.text.startsWith('[set]') || item.text.includes('=')) color = '#4ade80';

              return (
                <div key={item.id} style={{ ...styles.logLine, color }}>
                  <span style={styles.logTimestamp}>
                    [{new Date(item.timestamp).toLocaleTimeString()}]
                  </span>
                  <span style={styles.logText}>{item.text}</span>
                </div>
              );
            })}
            <div ref={logEndRef} />
          </div>

          {/* Side Macro Drawer */}
          {drawerOpen && (
            <div style={styles.macroDrawer}>
              <div style={styles.drawerHeader}>
                <strong>Python Macros</strong>
                <span style={{ fontSize: '0.7rem', color: '#94a3b8' }}>
                  {macros.length} scripts
                </span>
              </div>
              <div style={styles.macroList}>
                {macros.map((m) => (
                  <div
                    key={m.name}
                    style={{
                      ...styles.macroCard,
                      ...(selectedMacro?.name === m.name ? styles.macroCardActive : null),
                    }}
                    onClick={() => setSelectedMacro(m)}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <strong style={{ color: '#38bdf8' }}>{m.name}</strong>
                      <span style={styles.macroBadge}>{m.builtin ? 'builtin' : 'user'}</span>
                    </div>
                    <p style={styles.macroDesc}>{m.description || 'No description'}</p>
                    <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          void runCommand(`macro.run("${m.name}")`);
                        }}
                        style={styles.macroRunBtn}
                      >
                        ▶ Run
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setInputVal(m.code);
                          setMultiLine(true);
                        }}
                        style={styles.macroEditBtn}
                      >
                        ✏️ Edit
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Autocomplete Float Popover */}
        {suggestions.length > 0 && (
          <div style={styles.autocompleteBox}>
            {suggestions.map((item, idx) => (
              <div
                key={item.name}
                style={{
                  ...styles.autoItem,
                  ...(idx === selectedSugIdx ? styles.autoItemActive : null),
                }}
                onClick={() => {
                  setInputVal(item.name + (item.kind === 'cvar' ? ' ' : ''));
                  setSuggestions([]);
                  inputRef.current?.focus();
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span style={styles.kindBadge}>{item.kind.toUpperCase()}</span>
                  <strong style={{ color: idx === selectedSugIdx ? '#ffffff' : '#38bdf8' }}>
                    {item.name}
                  </strong>
                  {item.kind === 'cvar' && (
                    <span style={{ color: '#94a3b8', fontSize: '0.72rem' }}>
                      = {String(item.currentValue)} (def: {String(item.defaultValue)})
                    </span>
                  )}
                </div>
                <span style={styles.autoDesc}>{item.signature || item.description}</span>
              </div>
            ))}
          </div>
        )}

        {/* Input Bar */}
        <div style={styles.inputBar}>
          <span style={styles.promptSymbol}>&gt;</span>
          {multiLine ? (
            <textarea
              ref={inputRef as React.RefObject<HTMLTextAreaElement>}
              value={inputVal}
              onChange={(e) => handleInputChange(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Enter Python code / Macro commands (Shift+Enter for newline, Enter to run)..."
              style={styles.textAreaInput}
              rows={3}
            />
          ) : (
            <input
              ref={inputRef as React.RefObject<HTMLInputElement>}
              type="text"
              value={inputVal}
              onChange={(e) => handleInputChange(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Enter command or Python script (e.g. draw.hitboxes 1, server.bots.add(3), help)..."
              style={styles.textInput}
              autoFocus
            />
          )}

          <button
            onClick={() => setMultiLine(!multiLine)}
            style={{
              ...styles.modeToggleBtn,
              ...(multiLine ? styles.modeToggleBtnActive : null),
            }}
            title={multiLine ? 'Switch to single-line prompt' : 'Switch to multi-line script editor'}
          >
            {multiLine ? 'Single' : 'Multi'}
          </button>

          <button
            onClick={() => {
              const cmd = inputVal;
              setInputVal('');
              setSuggestions([]);
              void runCommand(cmd);
            }}
            style={styles.submitBtn}
          >
            Execute
          </button>
        </div>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Styles
// -----------------------------------------------------------------------------

const styles: Record<string, React.CSSProperties> = {
  overlayContainer: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    height: '62%',
    zIndex: 1000,
    display: 'flex',
    flexDirection: 'column',
    background: 'rgba(10, 14, 20, 0.94)',
    backdropFilter: 'blur(8px)',
    borderBottom: '2px solid #38bdf8',
    boxShadow: '0 12px 36px rgba(0, 0, 0, 0.75)',
    animation: 'slideDown 0.18s cubic-bezier(0.16, 1, 0.3, 1)',
  },
  standaloneContainer: {
    width: '100%',
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    background: '#090d14',
    color: '#e2e8f0',
  },
  window: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    width: '100%',
    fontFamily: 'Consolas, "Roboto Mono", "Courier New", monospace',
    fontSize: '0.8rem',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '6px 12px',
    background: 'rgba(15, 23, 42, 0.9)',
    borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
  },
  titleBadge: {
    color: '#38bdf8',
    fontWeight: 700,
    letterSpacing: '0.08em',
    fontSize: '0.82rem',
  },
  statusChip: {
    fontSize: '0.72rem',
    color: '#94a3b8',
    background: 'rgba(30, 41, 59, 0.6)',
    padding: '2px 6px',
    borderRadius: 3,
    border: '1px solid rgba(255, 255, 255, 0.08)',
  },
  filterGroup: {
    display: 'flex',
    gap: '2px',
    background: 'rgba(30, 41, 59, 0.5)',
    borderRadius: 4,
    padding: '2px',
  },
  filterBtn: {
    background: 'transparent',
    border: 'none',
    color: '#94a3b8',
    fontSize: '0.7rem',
    padding: '2px 6px',
    borderRadius: 3,
    cursor: 'pointer',
  },
  filterBtnActive: {
    background: '#38bdf8',
    color: '#0f172a',
    fontWeight: 600,
  },
  actionBtn: {
    background: 'rgba(30, 41, 59, 0.7)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    color: '#cbd5e1',
    fontSize: '0.74rem',
    padding: '3px 8px',
    borderRadius: 4,
    cursor: 'pointer',
  },
  actionBtnActive: {
    background: 'rgba(56, 189, 248, 0.2)',
    borderColor: '#38bdf8',
    color: '#38bdf8',
  },
  closeBtn: {
    background: 'transparent',
    border: 'none',
    color: '#f87171',
    fontWeight: 700,
    fontSize: '0.9rem',
    cursor: 'pointer',
    padding: '2px 6px',
  },
  quickBar: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '5px 12px',
    background: 'rgba(15, 23, 42, 0.6)',
    borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
    flexWrap: 'wrap',
  },
  quickBtn: {
    background: 'rgba(30, 41, 59, 0.8)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    color: '#cbd5e1',
    fontSize: '0.74rem',
    padding: '3px 8px',
    borderRadius: 4,
    cursor: 'pointer',
  },
  quickBtnActive: {
    background: 'rgba(74, 222, 128, 0.2)',
    borderColor: '#4ade80',
    color: '#4ade80',
    fontWeight: 600,
  },
  quickBtnSmall: {
    background: 'rgba(30, 41, 59, 0.8)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    color: '#cbd5e1',
    fontSize: '0.7rem',
    padding: '2px 5px',
    borderRadius: 3,
    cursor: 'pointer',
  },
  timescaleContainer: {
    display: 'flex',
    alignItems: 'center',
    gap: '3px',
    background: 'rgba(30, 41, 59, 0.4)',
    padding: '2px 5px',
    borderRadius: 4,
  },
  bodyWrapper: {
    flex: 1,
    display: 'flex',
    minHeight: 0,
    position: 'relative',
  },
  logOutput: {
    flex: 1,
    padding: '8px 12px',
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: '3px',
    background: 'rgba(10, 14, 20, 0.8)',
  },
  logLine: {
    display: 'flex',
    gap: '8px',
    lineHeight: 1.4,
    wordBreak: 'break-word',
    whiteSpace: 'pre-wrap',
  },
  logTimestamp: {
    color: '#64748b',
    fontSize: '0.72rem',
    flexShrink: 0,
  },
  logText: {
    flex: 1,
  },
  macroDrawer: {
    width: 260,
    background: 'rgba(15, 23, 42, 0.95)',
    borderLeft: '1px solid rgba(255, 255, 255, 0.1)',
    display: 'flex',
    flexDirection: 'column',
    padding: '8px',
    overflowY: 'auto',
  },
  drawerHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '8px',
    paddingBottom: '4px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
  },
  macroList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
  macroCard: {
    background: 'rgba(30, 41, 59, 0.5)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: 4,
    padding: '6px 8px',
    cursor: 'pointer',
  },
  macroCardActive: {
    borderColor: '#38bdf8',
    background: 'rgba(56, 189, 248, 0.1)',
  },
  macroBadge: {
    fontSize: '0.65rem',
    background: 'rgba(255, 255, 255, 0.08)',
    padding: '1px 4px',
    borderRadius: 2,
    color: '#94a3b8',
  },
  macroDesc: {
    fontSize: '0.7rem',
    color: '#94a3b8',
    margin: '3px 0 0 0',
    lineHeight: 1.25,
  },
  macroRunBtn: {
    background: '#38bdf8',
    color: '#0f172a',
    border: 'none',
    borderRadius: 3,
    fontSize: '0.68rem',
    fontWeight: 600,
    padding: '2px 6px',
    cursor: 'pointer',
  },
  macroEditBtn: {
    background: 'rgba(255, 255, 255, 0.1)',
    color: '#e2e8f0',
    border: 'none',
    borderRadius: 3,
    fontSize: '0.68rem',
    padding: '2px 6px',
    cursor: 'pointer',
  },
  autocompleteBox: {
    position: 'absolute',
    bottom: 44,
    left: 12,
    right: 12,
    background: 'rgba(15, 23, 42, 0.98)',
    border: '1px solid #38bdf8',
    borderRadius: 4,
    maxHeight: 180,
    overflowY: 'auto',
    zIndex: 100,
    boxShadow: '0 -6px 20px rgba(0,0,0,0.6)',
  },
  autoItem: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '5px 10px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.05)',
    cursor: 'pointer',
  },
  autoItemActive: {
    background: 'rgba(56, 189, 248, 0.25)',
  },
  kindBadge: {
    fontSize: '0.62rem',
    background: '#38bdf8',
    color: '#0f172a',
    fontWeight: 700,
    padding: '1px 4px',
    borderRadius: 2,
  },
  autoDesc: {
    fontSize: '0.72rem',
    color: '#94a3b8',
    maxWidth: '50%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  inputBar: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '8px 12px',
    background: 'rgba(15, 23, 42, 0.95)',
    borderTop: '1px solid rgba(255, 255, 255, 0.1)',
  },
  promptSymbol: {
    color: '#38bdf8',
    fontWeight: 700,
    fontSize: '1rem',
  },
  textInput: {
    flex: 1,
    background: 'rgba(30, 41, 59, 0.6)',
    border: '1px solid rgba(255, 255, 255, 0.15)',
    borderRadius: 4,
    padding: '6px 10px',
    color: '#ffffff',
    fontFamily: 'inherit',
    fontSize: '0.82rem',
    outline: 'none',
  },
  textAreaInput: {
    flex: 1,
    background: 'rgba(30, 41, 59, 0.6)',
    border: '1px solid rgba(255, 255, 255, 0.15)',
    borderRadius: 4,
    padding: '6px 10px',
    color: '#ffffff',
    fontFamily: 'inherit',
    fontSize: '0.82rem',
    outline: 'none',
    resize: 'vertical',
  },
  modeToggleBtn: {
    background: 'rgba(30, 41, 59, 0.8)',
    border: '1px solid rgba(255, 255, 255, 0.12)',
    color: '#cbd5e1',
    padding: '6px 8px',
    borderRadius: 4,
    fontSize: '0.74rem',
    cursor: 'pointer',
  },
  modeToggleBtnActive: {
    background: 'rgba(168, 85, 247, 0.25)',
    borderColor: '#a855f7',
    color: '#a855f7',
  },
  submitBtn: {
    background: '#38bdf8',
    color: '#0f172a',
    border: 'none',
    padding: '6px 14px',
    borderRadius: 4,
    fontWeight: 700,
    fontSize: '0.78rem',
    cursor: 'pointer',
  },
};
