import { useEffect, useState } from 'react';

import { openExternal } from './external';
import { IconCheck, IconCopy } from './glyphs';

/**
 * A link that cannot dead-end.
 *
 * Every other link in the app is a plain `<a>`, and that is fine — a docs link
 * that does nothing is an annoyance. This one is for the places where the link
 * *is* the recovery path: an OAuth consent page nobody managed to open, a device
 * verification URL. There, a link that silently does nothing leaves the user with
 * no way forward at all, and the flow behind it keeps polling for a page they
 * were never shown.
 *
 * So it opens through {@link openExternal} (which reports failure) rather than
 * through the browser's own navigation, and when that fails it stops offering a
 * link — clicking is what just failed — and shows the address as selectable text
 * with a Copy button instead. The address stays on screen from then on.
 *
 * `showCopy` adds a copy control to the *working* state as well. Off by default,
 * because for most callers the link is somewhere to go and copying it is not the
 * point. It is opt-in rather than universal for the share link's reason: there
 * the URL **is** the artefact being handed to someone else, so clicking it is the
 * least useful thing you can do with it.
 */
export function CopyableLink({
  url,
  label,
  className,
  showCopy = false,
}: {
  url: string;
  /** Link text. The URL itself is shown once opening has failed. */
  label: string;
  className?: string;
  /** Show a copy button beside the link, not only in the failed state. */
  showCopy?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const [copied, setCopied] = useState<boolean | null>(null);

  const open = () => {
    void openExternal(url).then((opened) => {
      if (!opened) setFailed(true);
    });
  };

  const copy = () => {
    void navigator.clipboard
      .writeText(url)
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  };

  // The tick is feedback, not a state worth keeping: a button still reading
  // "Copied" a minute later says nothing about the clipboard, which by then may
  // hold something else entirely.
  useEffect(() => {
    if (copied !== true) return;
    const t = setTimeout(() => setCopied(null), 1600);
    return () => clearTimeout(t);
  }, [copied]);

  if (failed) {
    return (
      <span className={`copyable-link failed${className ? ` ${className}` : ''}`}>
        <code className="copyable-link-url">{url}</code>
        <button type="button" className="copyable-link-btn" onClick={copy}>
          {copied === true ? 'Copied' : copied === false ? 'Select and copy' : 'Copy link'}
        </button>
      </span>
    );
  }

  const anchor = (
    <a
      className={`copyable-link${className ? ` ${className}` : ''}`}
      href={url}
      target="_blank"
      rel="noreferrer"
      onClick={(e) => {
        // Take over from the browser so the failure is observable. Under the
        // desktop shell the link bridge would intercept this anyway; doing it
        // here means the fallback renders in place, next to the flow it belongs
        // to, rather than as a detached toast.
        e.preventDefault();
        open();
      }}
    >
      {label}
    </a>
  );

  if (!showCopy) return anchor;

  return (
    <span className="copyable-link-row">
      {anchor}
      <button
        type="button"
        className="copyable-link-icon"
        onClick={copy}
        // The label carries the state, because the icon swap is the one part of
        // this a screen reader cannot see.
        aria-label={
          copied === true ? 'Copied' : copied === false ? 'Could not copy' : 'Copy link'
        }
        title={copied === false ? 'Could not copy — select the link instead' : 'Copy link'}
        data-copied={copied === true ? 'true' : undefined}
      >
        {copied === true ? <IconCheck /> : <IconCopy />}
      </button>
    </span>
  );
}

/**
 * A literal value the user has to put somewhere else — an environment variable
 * name, a config key, a path.
 *
 * The sibling of {@link CopyableLink}, and it exists for the same reason: when a
 * name appears inside a sentence, the only thing a user can do with it is retype
 * it, and retyping is where the typo comes from. So the value gets the recessed
 * well and the mono treatment that marks it as a literal, plus the one control
 * that makes it actionable.
 */
export function CopyableValue({
  value,
  label,
  className,
}: {
  value: string;
  /** What the value *is*, so the well is not an unlabelled string. */
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState<boolean | null>(null);

  const copy = () => {
    void navigator.clipboard
      .writeText(value)
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  };

  return (
    <span className={`copyable-value${className ? ` ${className}` : ''}`}>
      {label && <span className="copyable-value-label">{label}</span>}
      <code className="copyable-value-code">{value}</code>
      <button type="button" className="copyable-link-btn" onClick={copy}>
        {copied === true ? 'Copied' : copied === false ? 'Select and copy' : 'Copy'}
      </button>
    </span>
  );
}
