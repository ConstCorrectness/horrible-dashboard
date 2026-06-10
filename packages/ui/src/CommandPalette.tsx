import { useEffect, useRef, useState } from 'react';
import { registry } from '@horrible/core';

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery('');
      setSelected(0);
      inputRef.current?.focus();
    }
  }, [open]);

  if (!open) return null;

  const matches = registry.commands.filter((c) =>
    c.title.toLowerCase().includes(query.toLowerCase()),
  );

  const run = (index: number) => {
    const command = matches[index];
    onClose();
    if (command) void registry.runCommand(command.id);
  };

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          value={query}
          placeholder="Type a command…"
          onChange={(e) => {
            setQuery(e.target.value);
            setSelected(0);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose();
            if (e.key === 'Enter') run(selected);
            if (e.key === 'ArrowDown') setSelected((s) => Math.min(s + 1, matches.length - 1));
            if (e.key === 'ArrowUp') setSelected((s) => Math.max(s - 1, 0));
          }}
        />
        <ul>
          {matches.map((c, i) => (
            <li key={c.id} className={i === selected ? 'selected' : ''} onClick={() => run(i)}>
              <span>{c.title}</span>
              <code>{c.id}</code>
            </li>
          ))}
          {matches.length === 0 && <li className="empty">No matching commands</li>}
        </ul>
      </div>
    </div>
  );
}
