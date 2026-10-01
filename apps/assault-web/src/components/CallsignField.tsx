import { useEffect, useState } from 'react';

/**
 * The guest's name, as a field that looks like one.
 *
 * It used to be the name in display type with a quiet "Rename" beside it, and
 * nobody read that as editable. Now it is an input from the start: type, and it
 * saves on Enter or when focus leaves. An empty name falls back to a generated
 * one in `useGuestSession`, so there is nothing to refuse.
 */
export function CallsignField({
  callsign,
  onChange,
  label = 'Guest callsign',
  autoFocus = false,
}: {
  callsign: string;
  onChange: (name: string) => void;
  label?: string;
  autoFocus?: boolean;
}) {
  const [draft, setDraft] = useState(callsign);
  // Follow the stored name when it changes elsewhere (the gate, another tab).
  useEffect(() => setDraft(callsign), [callsign]);

  const commit = () => {
    if (draft.trim() !== callsign) onChange(draft);
  };

  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="text"
        className="input callsign-input"
        maxLength={16}
        value={draft}
        spellCheck={false}
        autoComplete="nickname"
        autoFocus={autoFocus}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            commit();
            (e.target as HTMLInputElement).blur();
          }
        }}
      />
    </label>
  );
}
