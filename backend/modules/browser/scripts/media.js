() => {
  const abs = (u) => { try { return new URL(u, location.href).href; } catch { return null; } };
  const clip = (s, n) => { s = (s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

  // The words that describe an element: its own labels, then its <figure>
  // caption, then the nearest preceding heading. Ordered most- to least-specific.
  const describe = (el) => {
    const parts = [];
    const fig = el.closest('figure');
    const cap = fig && fig.querySelector('figcaption');
    if (cap) parts.push(clip(cap.innerText, 300));
    let node = el, heading = null;
    while (node && !heading) {
      let sib = node.previousElementSibling;
      while (sib && !heading) {
        if (/^H[1-6]$/.test(sib.tagName)) heading = sib;
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
    }
    if (heading) parts.push(clip(heading.innerText, 160));
    return parts;
  };

  const images = [];
  for (const el of document.querySelectorAll('img')) {
    const src = abs(el.currentSrc || el.src);
    if (!src || src.startsWith('data:')) continue;      // inline pixels aren't addressable
    const w = el.naturalWidth || el.width, h = el.naturalHeight || el.height;
    if (w && h && w < 64 && h < 64) continue;           // spacers, icons, tracking pixels
    images.push({
      src, kind: 'image',
      alt: clip(el.alt, 300),
      title: clip(el.getAttribute('title'), 160),
      width: w || null, height: h || null,
      context: describe(el),
    });
    if (images.length >= 100) break;
  }

  const videos = [];
  for (const el of document.querySelectorAll('video, iframe')) {
    let src = null, kind = 'video';
    if (el.tagName === 'VIDEO') {
      src = abs(el.currentSrc || el.src);
      if (!src) { const s = el.querySelector('source'); if (s) src = abs(s.src); }
    } else {
      // Only embeds that are actually video players — a generic iframe isn't media.
      const u = abs(el.src) || '';
      if (!/(youtube|youtube-nocookie|vimeo|dailymotion|player\.twitch)\./.test(u)) continue;
      src = u; kind = 'embed';
    }
    if (!src) continue;
    videos.push({
      src, kind,
      alt: clip(el.getAttribute('aria-label') || el.getAttribute('title'), 300),
      title: clip(el.getAttribute('title'), 160),
      width: el.videoWidth || el.width || null,
      height: el.videoHeight || el.height || null,
      duration: (el.duration && isFinite(el.duration)) ? Math.round(el.duration) : null,
      poster: el.poster ? abs(el.poster) : null,
      context: describe(el),
    });
    if (videos.length >= 50) break;
  }

  return { url: location.href, title: document.title, images, videos };
}
