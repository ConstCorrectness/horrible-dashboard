import { useEffect, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { createInvite, type InviteResponse } from '@horrible/core';

interface Props {
  onClose: () => void;
}

export function MobilePairingDialog({ onClose }: Props) {
  const [invite, setInvite] = useState<InviteResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    createInvite()
      .then(setInvite)
      .catch((err: unknown) => setError(String(err)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="integration-popover mobile-pairing-popover" role="dialog" aria-label="Pair Mobile Device">
      <div className="mobile-pairing-header">
        <h3>Pair Mobile</h3>
        <button type="button" className="close-button" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      <div className="mobile-pairing-body">
        {loading && <div className="pairing-status">Generating...</div>}
        {error && <div className="pairing-status error">{error}</div>}
        {invite && (
          <div className="pairing-qr-container">
            <div className="pairing-qr">
              <QRCodeSVG value={`horrible://pair?invite=${invite.invite}`} size={180} marginSize={2} />
            </div>
            <p className="pairing-instructions">
              Scan with the mobile app to pair.
            </p>
            <div className="pairing-details">
              <span>Expires in {Math.round((invite.expires - Date.now() / 1000) / 60)}m</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
