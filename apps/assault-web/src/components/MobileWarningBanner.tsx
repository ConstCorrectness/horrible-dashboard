import { useEffect, useState } from 'react';

export function MobileWarningBanner() {
  const [isMobile, setIsMobile] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const hasTouch =
      'ontouchstart' in window ||
      navigator.maxTouchPoints > 0 ||
      /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
    setIsMobile(hasTouch);
  }, []);

  if (!isMobile || dismissed) return null;

  return (
    <div className="banner" role="status">
      <span>
        <b>Best on a desktop.</b> HorribleAssault aims with a locked mouse pointer and moves with
        WASD. Touch controls are a preview.
      </span>
      <button type="button" className="btn btn-sm" onClick={() => setDismissed(true)}>
        Dismiss
      </button>
    </div>
  );
}
