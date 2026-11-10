(target) => {
  // What acting on one element would do, so the agent's permission gate can tell a
  // link from a form submission (packages/core/src/modules/browser/action-gate.ts).
  // `target` is a snapshot ref; {x, y} is whatever is at that viewport point (a
  // pointer click), taken up to the control it belongs to; null describes whatever
  // has focus (a key press).
  // Never returns a field's value: only whether a password field holds one.
  const CONTROL =
    'a[href], button, input, select, textarea, summary, label, [role=button],' +
    '[role=link], [role=tab], [role=menuitem], [role=checkbox], [onclick],' +
    '[contenteditable=true]';
  let el;
  if (target === null || target === undefined) el = document.activeElement;
  else if (typeof target === 'object') {
    const hit = document.elementFromPoint(Number(target.x), Number(target.y));
    el = hit ? hit.closest(CONTROL) || hit : null;
  } else el = document.querySelector('[data-agent-ref="' + Math.trunc(Number(target)) + '"]');
  if (!el || el === document.body || el === document.documentElement) return null;
  const clip = (s, n) => {
    const t = String(s || '')
      .replace(/\s+/g, ' ')
      .trim();
    return t.length > n ? t.slice(0, n - 3) + '...' : t;
  };
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  const labelOf = (node) =>
    clip(
      node.getAttribute('aria-label') ||
        (node.tagName === 'INPUT' ? node.value : node.innerText) ||
        node.getAttribute('title') ||
        node.getAttribute('alt') ||
        node.getAttribute('placeholder') ||
        '',
      80,
    );
  const form = el.form || el.closest('form');
  const link = el.closest('a[href]');
  const isButton = tag === 'button' || el.getAttribute('role') === 'button';
  const submits = Boolean(
    form &&
    ((tag === 'button' && (type === '' || type === 'submit')) ||
      (tag === 'input' && (type === 'submit' || type === 'image'))),
  );
  // The button Enter in a field would press: the form's first submitter.
  let submitter = '';
  if (form) {
    const btn = form.querySelector(
      'button:not([type]), button[type=submit], input[type=submit], input[type=image]',
    );
    if (btn) submitter = labelOf(btn);
  }
  const passwords = form ? Array.from(form.querySelectorAll('input[type=password]')) : [];
  return {
    tag,
    type,
    role: el.getAttribute('role') || '',
    name: labelOf(el),
    fieldName: clip(el.getAttribute('name') || el.id || '', 60),
    autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
    href: link ? String(link.href) : '',
    isButton,
    editable:
      el.isContentEditable ||
      tag === 'textarea' ||
      (tag === 'input' &&
        type !== 'submit' &&
        type !== 'button' &&
        type !== 'image' &&
        type !== 'checkbox' &&
        type !== 'radio'),
    expandable: el.hasAttribute('aria-expanded') || tag === 'summary' || tag === 'select',
    inForm: Boolean(form),
    formMethod: form ? (form.getAttribute('method') || 'get').toLowerCase() : '',
    formAction: form ? String(form.action || '') : '',
    formHasPassword: passwords.length > 0,
    formPasswordFilled: passwords.some((p) => p.value.length > 0),
    submits,
    submitter,
    // A data: or file: page has no host; name it by its scheme instead.
    host: location.host || location.protocol.replace(':', ''),
    // Which document this is (minted by snapshot.js): a ref from another one is stale.
    doc: (document.documentElement.__horribleAgentRefs || {}).doc || '',
  };
};
