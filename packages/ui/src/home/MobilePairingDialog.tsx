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
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="mobile-pairing-dialog" role="dialog" aria-label="Pair Mobile Device">
      <div className="mobile-pairing-header">
        <h3>Pair Mobile Device</h3>
        <button type="button" className="close-button" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      <div className="mobile-pairing-body">
        {loading && <div className="pairing-status">Generating invite...</div>}
        {error && <div className="pairing-status error">{error}</div>}
        {invite && (
          <div className="pairing-qr-container">
            <div className="pairing-qr">
              <QRCodeSVG value={invite.invite} size={256} marginSize={4} />
            </div>
            <p className="pairing-instructions">
              Scan this QR code with the Horrible Dashboard mobile app to pair your phone.
            </p>
            <div className="pairing-details">
              <span>Token expires in {Math.round((invite.expires - Date.now() / 1000) / 60)}m</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
