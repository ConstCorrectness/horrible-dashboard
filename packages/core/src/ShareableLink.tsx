import { useEffect, useState, type ReactNode } from 'react';
import { QRCodeSVG } from 'qrcode.react';

import { CopyableLink } from './CopyableLink';
import { IconCheck, IconCopy, IconQr } from './glyphs';
import { EXPIRY_WARN_S, displayUrl, formatRemaining, inviteText, secondsLeft } from './link-format';
import { Button, Chip } from './Primitives';

import './ShareableLink.css';

/**
 * A link that exists to be handed to somebody else.
 *
 * {@link CopyableLink} is for a URL you *go to*; this is for a URL you *give
 * away*, which needs different things: the address short enough to read, a QR
 * code for the person across the room, an invite message that says what the
 * link is and when it stops working, and a visible clock — a link that dies
 * mid-sentence is a surprise only if nothing on screen was counting down.
 *
 * Used by the share session pane and the notebook publish panel.
 */
export function ShareableLink({
  url,
  lead,
  expiresAt = 0,
  passphrase = false,
  actions,
}: {
  url: string;
  /** What the link is for, first line of the invite — "Watch my screen". */
  lead?: string;
  /** Epoch seconds; 0 for a link that does not expire. */
  expiresAt?: number;
  /** Whether the link needs a passphrase. Said in the invite, never included. */
  passphrase?: boolean;
  /** Extra controls on the action row — typically Revoke. */
  actions?: ReactNode;
}) {
  const [qr, setQr] = useState(false);
  const [copied, setCopied] = useState<boolean | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Half-minute ticks: the chip reads in minutes, so anything faster re-renders
  // for nothing.
  useEffect(() => {
    if (!expiresAt) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, [expiresAt]);

  useEffect(() => {
    if (copied !== true) return;
    const timer = setTimeout(() => setCopied(null), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  const remaining = formatRemaining(expiresAt, now);
  const left = secondsLeft(expiresAt, now);

  const copyInvite = () => {
    void navigator.clipboard
      .writeText(inviteText({ url, lead, expiresAt, passphrase }))
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  };

  return (
    <div className="shareable-link">
      <div className="shareable-link__row">
        <CopyableLink url={url} label={displayUrl(url)} showCopy className="shareable-link__url" />
      </div>
      <div className="shareable-link__row">
        {remaining && (
          <Chip
            kind={left !== null && left < EXPIRY_WARN_S ? 'warn' : 'idle'}
            dot={remaining !== 'expired'}
            title={
              expiresAt ? `Stops working at ${new Date(expiresAt * 1000).toLocaleTimeString()}` : ''
            }
          >
            {remaining === 'expired' ? 'expired' : `expires in ${remaining}`}
          </Chip>
        )}
        {passphrase && <Chip kind="info">passphrase</Chip>}
        <span className="shareable-link__spacer" />
        <Button
          size="sm"
          intent="ghost"
          icon={<IconQr />}
          aria-pressed={qr}
          onClick={() => setQr((v) => !v)}
        >
          {qr ? 'Hide QR' : 'QR code'}
        </Button>
        <Button
          size="sm"
          intent="ghost"
          icon={copied === true ? <IconCheck /> : <IconCopy />}
          onClick={copyInvite}
          title="Copy a message with the link, its expiry, and whether it needs a passphrase"
        >
          {copied === true ? 'Invite copied' : copied === false ? 'Could not copy' : 'Copy invite'}
        </Button>
        {actions}
      </div>
      {qr && (
        <figure className="shareable-link__qr">
          {/* Always black on white, whatever the theme: a phone camera needs the
              contrast, and an inverted code fails to scan on many readers. The
              margin is the quiet zone the spec requires. */}
          <QRCodeSVG
            value={url}
            size={168}
            marginSize={2}
            bgColor="#ffffff"
            fgColor="#000000"
            title="QR code for this link"
          />
          <figcaption>Point a phone camera at this.</figcaption>
        </figure>
      )}
    </div>
  );
}
