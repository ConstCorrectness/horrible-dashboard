import { useEffect, useState } from 'react';
import { apiUrl } from '@horrible/core';
import { parseDownloadInfo, type NativeBuild } from '../download';

/**
 * The native client this server has for download, or null — while asking, when
 * there is none, and when the server could not be reached. All three show the
 * same thing (no button), so they are one value: the page has nothing useful to
 * say about a download it cannot offer.
 *
 * Asked once. The answer only changes with a deploy, and a deploy reloads the page.
 */
export function useNativeDownload(): NativeBuild | null {
  const [build, setBuild] = useState<NativeBuild | null>(null);

  useEffect(() => {
    let live = true;
    fetch(apiUrl('/download/info'), { credentials: 'omit' })
      .then((res) => (res.ok ? res.json() : null))
      .then((info) => {
        if (live) setBuild(parseDownloadInfo(info));
      })
      .catch(() => {
        /* no button */
      });
    return () => {
      live = false;
    };
  }, []);

  return build;
}
