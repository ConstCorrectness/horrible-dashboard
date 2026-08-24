import { useState } from 'react';

import { openExternal } from './external';

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
 */
export function CopyableLink({
  url,
  label,
  className,
}: {
  url: string;
  /** Link text. The URL itself is shown once opening has failed. */
  label: string;
  className?: string;
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

  return (
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
