() => {
  const SEL =
    'a[href], button, input, select, textarea, [role=button],' +
    '[role=link], [role=tab], [role=menuitem], [role=checkbox], [onclick],' +
    '[contenteditable=true]';
  const out = [];
  let ref = 0;
  const nodes = document.querySelectorAll(SEL);
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    const visible =
      rect.width > 0 &&
      rect.height > 0 &&
      rect.bottom > 0 &&
      rect.right > 0 &&
      rect.top < innerHeight &&
      rect.left < innerWidth &&
      getComputedStyle(el).visibility !== 'hidden' &&
      getComputedStyle(el).display !== 'none';
    if (!visible) continue;
    ref += 1;
    el.setAttribute('data-agent-ref', String(ref));
    const role =
      el.getAttribute('role') ||
      (el.tagName === 'A'
        ? 'link'
        : el.tagName === 'BUTTON'
          ? 'button'
          : el.tagName === 'INPUT'
            ? el.type || 'textbox'
            : el.tagName.toLowerCase());
    let name = (
      el.getAttribute('aria-label') ||
      el.innerText ||
      el.value ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.getAttribute('alt') ||
      ''
    ).trim();
    if (name.length > 120) name = name.slice(0, 117) + '...';
    out.push({
      ref,
      role,
      name,
      value: el.value !== undefined ? String(el.value).slice(0, 120) : '',
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
    });
    if (ref >= 200) break;
  }
  // A bot check the agent must not try to solve: it hands the page to the human.
  const CHALLENGE = /captcha|hcaptcha|turnstile|challenges\.cloudflare\.com|arkoselabs|funcaptcha/i;
  const challenge =
    Array.from(document.querySelectorAll('iframe')).some(
      (f) => CHALLENGE.test(f.src || '') || CHALLENGE.test(f.title || ''),
    ) || /^(just a moment|attention required)/i.test(document.title);
  return { url: location.href, title: document.title, elements: out, challenge };
};
