(opts) => {
  // The page as the agent sees it: a numbered list of what can be acted on.
  //
  // Refs are **stable within a document**: an element keeps the ref it was first
  // given, so a ref from any earlier look at this page still names the same
  // element, and a scroll or a `find` never renumbers what the agent already holds.
  // `doc` is a random id minted once per document. A navigation is a new document
  // (a new JS realm), so a new id: the agent's tools compare it to the id the ref
  // came with and refuse a ref from a page that has gone (agentTools.ts).
  //
  // opts (all optional):
  //   fullPage: list elements anywhere on the page, not only on screen
  //   query:    only elements whose role, name, value or link match (any position)
  //   limit:    cap on elements returned (default 200, at most 400)
  const o = opts || {};
  const query = typeof o.query === 'string' ? o.query.trim().toLowerCase() : '';
  const anywhere = Boolean(o.fullPage) || query !== '';
  const limit = Math.max(1, Math.min(400, Math.trunc(Number(o.limit) || 200)));

  // Kept on the root element, not the window: a document rewritten in place
  // (document.open/write) keeps its window but gets a new root, and is a new page.
  const KEY = '__horribleAgentRefs';
  const root = document.documentElement;
  let st = root[KEY];
  if (!st || typeof st.doc !== 'string' || !(st.map instanceof WeakMap)) {
    st = { doc: Math.random().toString(36).slice(2, 10), next: 0, map: new WeakMap() };
    Object.defineProperty(root, KEY, { value: st, configurable: true });
  }

  let sel =
    'a[href], button, input, select, textarea, [role=button],' +
    '[role=link], [role=tab], [role=menuitem], [role=checkbox], [onclick],' +
    '[contenteditable=true]';
  // A search may be looking for a section to scroll to, not only a control.
  if (query) sel += ', h1, h2, h3, h4, h5, h6, [role=heading]';

  const out = [];
  let total = 0;
  for (const el of document.querySelectorAll(sel)) {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    const onScreen =
      rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
    if (!anywhere && !onScreen) continue;

    const role =
      el.getAttribute('role') ||
      (el.tagName === 'A'
        ? 'link'
        : el.tagName === 'BUTTON'
          ? 'button'
          : el.tagName === 'INPUT'
            ? el.type || 'textbox'
            : /^H[1-6]$/.test(el.tagName)
              ? 'heading'
              : el.tagName.toLowerCase());
    let name = (
      el.getAttribute('aria-label') ||
      el.innerText ||
      el.value ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.getAttribute('alt') ||
      ''
    )
      .replace(/\s+/g, ' ')
      .trim();
    if (name.length > 120) name = name.slice(0, 117) + '...';
    const value = el.value !== undefined ? String(el.value).slice(0, 120) : '';
    if (query) {
      const hay = (
        role +
        ' ' +
        name +
        ' ' +
        value +
        ' ' +
        (el.getAttribute('href') || '')
      ).toLowerCase();
      if (!hay.includes(query)) continue;
    }
    total += 1;
    if (out.length >= limit) continue;

    // An element the page cloned carries a copied attribute but is not the one we
    // tagged: it gets its own ref, so one ref never names two elements.
    let ref = st.map.get(el);
    if (ref === undefined) {
      st.next += 1;
      ref = st.next;
      st.map.set(el, ref);
    }
    el.setAttribute('data-agent-ref', String(ref));
    out.push({
      ref,
      role,
      name,
      value,
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
      onScreen,
    });
  }
  // A bot check the agent must not try to solve: it hands the page to the human.
  const CHALLENGE = /captcha|hcaptcha|turnstile|challenges\.cloudflare\.com|arkoselabs|funcaptcha/i;
  const challenge =
    Array.from(document.querySelectorAll('iframe')).some(
      (f) => CHALLENGE.test(f.src || '') || CHALLENGE.test(f.title || ''),
    ) || /^(just a moment|attention required)/i.test(document.title);
  return {
    url: location.href,
    title: document.title,
    doc: st.doc,
    // CSS pixels: the coordinate space of `x`/`y` here and of browser.pointer.
    viewport: { width: innerWidth, height: innerHeight },
    elements: out,
    total,
    challenge,
  };
};
