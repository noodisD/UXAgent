/* =========================================================================
 *  DOM "stripper" — keeps empty controls **and** guarantees unique
 *  parser-semantic-id values by appending numeric suffixes
 * ========================================================================= */

const parse = () => {
  /* ---------- globals --------------------------------------------------- */
  // Not 'picture': its <img alt> is often a control's only name. With it
  // blacklisted, every colour chip on the Hyundai configurator reached the
  // persona as an unnamed <button>, and the report built false findings on
  // colours the persona picked blind. Its <source> children are empty and
  // are pruned like any other empty element.
  const BLACKLISTED_TAGS = new Set([
    'script', 'style', 'link', 'meta', 'noscript', 'template',
    'iframe', 'svg', 'canvas', 'video', 'audio',
    'object', 'embed'
  ]);

  // Elements that are controls in their own right, even inside another
  // clickable element — a checkbox or an "info" button inside a clickable
  // card. They used to get no id there, and when their only content was an
  // icon they vanished entirely: the package step's checkbox
  // (aria-label "checkbox Pakiet COMFORT") never reached the persona, and the
  // report said the step had no way to add a package.
  const CONTROL_TAGS = new Set(['button', 'input', 'select', 'textarea']);
  const CONTROL_ROLES = new Set([
    'button', 'checkbox', 'radio', 'switch', 'link', 'option', 'tab', 'menuitem'
  ]);
  const isRealControl = (el, tag) =>
    CONTROL_TAGS.has(tag) ||
    (tag === 'a' && el.hasAttribute('href')) ||
    CONTROL_ROLES.has(el.getAttribute('role') || '');

  // A control's name when it shows no text: its aria-label, or the alt of
  // the image it wraps (colour and interior chips are images only).
  const imageAlt = (el) => {
    const img = el.querySelector && el.querySelector('img[alt]');
    return img ? (img.getAttribute('alt') || '').trim() : '';
  };

  const ALLOWED_ATTR = new Set([
    'id', 'type', 'name', 'value', 'placeholder',
    'checked', 'disabled', 'readonly', 'required', 'maxlength',
    'min', 'max', 'step', 'role', 'tabindex', 'alt', 'title',
    'for', 'action', 'method', 'contenteditable', 'selected',
    'multiple', 'autocomplete'
  ]);

  const PRESERVE_EMPTY_TAGS = new Set([
    'input', 'select', 'textarea', 'button', 'img', 'head', 'title', 'form'
  ]);

  const USED_SEMANTIC_IDS = new Set();

  /* ---------- helpers -------------------------------------------------- */
  const copyAllowed = (src, dst) => {
    for (const a of src.attributes) {
      if (
        ALLOWED_ATTR.has(a.name) ||
        a.name.startsWith('aria-') ||
        a.name.startsWith('parser-')
      ) {
        dst.setAttribute(a.name, a.value);
      }
    }
  };

  const slug = (t) =>
    t.toLowerCase().replace(/\s+/g, ' ').trim()
      .replace(/[^\w]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 20);

  const uniqueName = (base) => {
    let name = base || 'item';
    if (!USED_SEMANTIC_IDS.has(name)) {
      USED_SEMANTIC_IDS.add(name);
      return name;
    }
    let i = 1;
    while (USED_SEMANTIC_IDS.has(name + i)) i++;
    USED_SEMANTIC_IDS.add(name + i);
    return name + i;
  };

  const isEmpty = (el) => {
    if (PRESERVE_EMPTY_TAGS.has(el.tagName.toLowerCase())) return false;
    // A named control, or anything carrying its own accessible name, is
    // content even with no text: an icon-only checkbox or link.
    if (el.hasAttribute('parser-semantic-id') ||
        (el.getAttribute('aria-label') || '').trim()) return false;
    for (const n of el.childNodes) {
      if (n.nodeType === 3 && n.textContent.trim()) return false;
      if (n.nodeType === 1 && !isEmpty(n)) return false;
    }
    return true;
  };

  const hasOnScreenFixedLayer = (el) => {
    const all = el.querySelectorAll('*');
    for (let i = 0; i < all.length && i < 3000; i++) {
      const pos = getComputedStyle(all[i]).position;
      if (pos !== 'fixed' && pos !== 'sticky') continue;
      const r = all[i].getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight) return true;
    }
    return false;
  };

  const isVisible = (el) => {
    // checkVisibility also catches hiding via ANCESTOR opacity:0 /
    // content-visibility, which the computed-style checks below miss
    // (opacity is not inherited, so a child of an opacity:0 flyout
    // reports opacity 1).
    if (typeof el.checkVisibility === 'function' &&
        !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) {
      return false;
    }
    const style = window.getComputedStyle(el);
    const hidden =
      style.display === 'none' ||
      style.visibility === 'hidden' ||
      parseFloat(style.opacity) === 0;

    const zeroSize = el.offsetWidth === 0 && el.offsetHeight === 0;

    const rect = el.getBoundingClientRect();
    const scrollLeft = window.scrollX || document.documentElement.scrollLeft;
    const right = rect.right;
    const top = rect.top;
    const outOfPort = (right + scrollLeft < 0);

    let belowPortNotScrollable = false;
    if (top > window.innerHeight && !(document.documentElement.scrollHeight > window.innerHeight)) {
      let hasScrollableAncestor = false;
      for (let p = el?.parentElement; p; p = p.parentElement) {
        const cs = getComputedStyle(p);
        const canScrollY = /(auto|scroll)/.test(cs.overflowY) && p.scrollHeight > p.clientHeight;
        if (canScrollY) { hasScrollableAncestor = true; break; }
      }
      // A wrapper laid out below the fold can still hold a fixed layer that is
      // on screen: a modal's portal, appended to the end of <body> on a page
      // whose scrolling the modal has locked. Dropping the wrapper dropped the
      // dialog — "Czy zapisać konfigurację na później?" was on screen while
      // the persona saw only the page beneath and called "Zamknij" broken.
      belowPortNotScrollable = !hasScrollableAncestor && !hasOnScreenFixedLayer(el);
    }
    if (hidden || zeroSize || outOfPort || belowPortNotScrollable) return false;
    return true;
  };

  const replaceElement = (el, newTag, child) => {
    const r = document.createElement(newTag);
    for (const a of el.attributes) r.setAttribute(a.name, a.value);
    copyAllowed(child, r);
    r.innerHTML = child.innerHTML;
    return r;
  };

  const pullUpChild = (parent, child) => {
    copyAllowed(child, parent);
    parent.innerHTML = child.innerHTML;
  };

  const flatten = (el) => {
    while (el.children.length === 1) {
      const child = el.children[0];
      const p = el.tagName.toLowerCase();
      const c = child.tagName.toLowerCase();
      if (p !== 'div' && c !== 'div') break;
      el = (p === 'div' && c !== 'div')
        ? replaceElement(el, child.tagName, child)
        : (pullUpChild(el, child), el);
    }
    return el;
  };

  /* ---------- clear parser-* attrs before run -------------------------- */
  (() => {
    const clearParserAttrs = (el) => {
      if (!el || !el.attributes) return;
      for (const a of Array.from(el.attributes)) {
        if (a.name.startsWith('parser-')) el.removeAttribute(a.name);
      }
    };

    // Clear on <html> itself first
    clearParserAttrs(document.documentElement);

    // Walk every element efficiently and clear any parser-* attrs
    const walker = document.createTreeWalker(document, NodeFilter.SHOW_ELEMENT);
    let node = walker.currentNode;
    while (node) {
      clearParserAttrs(node);
      node = walker.nextNode();
    }
  })();


  /* ==================================================================== */
  function automaticStripElement(original, parentName = '', parentIsClickable = false) {
    if (!original || original.nodeType !== 1) return null;
    const tag = original.tagName.toLowerCase();
    if (BLACKLISTED_TAGS.has(tag)) return null;
    if (!isVisible(original)) return null;

    let clone = document.createElement(original.tagName);
    copyAllowed(original, clone);

    const computedStyle = window.getComputedStyle(original);
    if (computedStyle.pointerEvents !== 'auto') {
      clone.setAttribute('parser-pointer-events', computedStyle.pointerEvents);
    }
    if (document.activeElement === original) {
      clone.setAttribute('parser-is-focused', 'true');
    }

    const isDisabled = original.disabled ||
      original.hasAttribute('disabled') ||
      computedStyle.pointerEvents === 'none';

    const probablyClickable = (() => {
      if (['button', 'select', 'summary', 'area', 'input'].includes(tag)) return true;
      if (tag === 'a' && original.hasAttribute('href')) return true;
      if (original.hasAttribute('onclick')) return true;
      const r = original.getAttribute('role');
      if (['button', 'link', 'checkbox', 'radio', 'option'].includes(r)) return true;
      return computedStyle.cursor === 'pointer';
    })();

    const isClickable = probablyClickable && !isDisabled &&
      (!parentIsClickable || isRealControl(original, tag));

    let thisName = '';
    if (isClickable) {
      const base = slug((original.innerText || '').trim() ||
        (original.getAttribute('aria-label') || '').trim() ||
        original.getAttribute('title') ||
        imageAlt(original) ||
        original.getAttribute('placeholder') ||
        tag);
      thisName = uniqueName(parentName ? `${parentName}.${base}` : base);
      for (const e of [clone, original]) {
        e.setAttribute('parser-semantic-id', thisName);
        e.setAttribute('parser-clickable', 'true');
      }
    }

    // initscript.js tags elements that register hover listeners with
    // data-maybe-hoverable (NOT parser-*, which is cleared each parse).
    // Skip page-sized containers: frameworks with delegated events tag the
    // app root, which would mark every element as hoverable.
    const hoverContainer = original.closest('[data-maybe-hoverable="true"]');
    if (hoverContainer &&
        hoverContainer !== document.body &&
        hoverContainer !== document.documentElement) {
      const hr = hoverContainer.getBoundingClientRect();
      if (hr.width * hr.height < window.innerWidth * window.innerHeight * 0.5) {
        clone.setAttribute('parser-maybe-hoverable', 'true');
        original.setAttribute('parser-maybe-hoverable', 'true');
      }
    }

    if (tag === 'input' || tag === 'textarea' || original.hasAttribute('contenteditable')) {
      const t = original.getAttribute('type') || 'text';
      const inputIsDisabled = original.disabled || original.readOnly;
      if (!inputIsDisabled && !thisName) {
        const base = slug((original.getAttribute('placeholder') ||
          original.getAttribute('name') ||
          original.value || '').trim() || tag);
        thisName = uniqueName(parentName ? `${parentName}.${base}` : base);
      }
      if (!inputIsDisabled && thisName) {
        clone.setAttribute('parser-semantic-id', thisName);
        clone.setAttribute('value', original.value || '');
        clone.setAttribute('parser-input-disabled', 'false');
        clone.setAttribute('parser-can-edit', !original.readOnly ? 'true' : 'false');
        original.setAttribute('parser-semantic-id', thisName);
      }
      if (!inputIsDisabled && thisName && t === 'number') {
        clone.setAttribute('parser-numeric-value', original.valueAsNumber || '');
      }
      if (!inputIsDisabled && thisName && original.selectionStart !== undefined) {
        clone.setAttribute('parser-selection-start', original.selectionStart);
        clone.setAttribute('parser-selection-end', original.selectionEnd);
      }
    }

    if (tag === 'select') {
      const selectIsDisabled = original.disabled || original.hasAttribute('disabled');
      if (!selectIsDisabled) {
        if (!thisName) {
          const base = slug((original.getAttribute('name') || tag));
          thisName = uniqueName(parentName ? `${parentName}.${base}` : base);
        }
        clone.setAttribute('parser-semantic-id', thisName);
        clone.setAttribute('parser-value', original.value);
        clone.setAttribute('parser-selected-index', original.selectedIndex);
        clone.setAttribute('parser-has-multiple', original.multiple ? 'true' : 'false');
        const selectedOptions = Array.from(original.selectedOptions).map(opt => opt.value).join(',');
        clone.setAttribute('parser-selected-values', selectedOptions);
        original.setAttribute('parser-semantic-id', thisName);
        for (const opt of original.querySelectorAll('option')) {
          const o = document.createElement('option');
          o.textContent = opt.textContent.trim();
          o.setAttribute('value', opt.value);
          o.setAttribute('parser-selected', opt.selected ? 'true' : 'false');
          const optName = uniqueName(`${thisName}.${slug(opt.textContent)}`);
          o.setAttribute('parser-semantic-id', optName);
          opt.setAttribute('parser-semantic-id', optName);
          clone.appendChild(o);
        }
      }
    }

    for (const child of original.children) {
      const cleaned = automaticStripElement(
        child,
        thisName || parentName,
        parentIsClickable || isClickable
      );
      if (cleaned && (!isEmpty(cleaned))) {
        clone.appendChild(cleaned);
      }
    }

    for (const n of original.childNodes) {
      if (n.nodeType === 3 && n.textContent.trim()) {
        clone.appendChild(document.createTextNode(n.textContent.trim()));
      }
    }

    clone = flatten(clone);
    for (let i = clone.children.length - 1; i >= 0; i--) {
      const c = clone.children[i];
      if (!PRESERVE_EMPTY_TAGS.has(c.tagName.toLowerCase()) && isEmpty(c)) {
        clone.removeChild(c);
      }
    }

    return clone;
  }

  const result = automaticStripElement(document.documentElement);
  return {
    html: result.outerHTML,
    clickable_elements: Array.from(result.querySelectorAll('[parser-clickable="true"]'))
      .map(el => el.getAttribute('parser-semantic-id')),
    hoverable_elements: Array.from(result.querySelectorAll('[parser-maybe-hoverable="true"][parser-semantic-id]'))
      .map(el => el.getAttribute('parser-semantic-id')),
    input_elements: Array.from(result.querySelectorAll('input[parser-semantic-id], textarea[parser-semantic-id], [contenteditable][parser-semantic-id]'))
      .map(el => ({
        id: el.getAttribute('parser-semantic-id'),
        disabled: el.hasAttribute('parser-input-disabled'),
        type: el.getAttribute('type') || (el.tagName.toLowerCase() === 'textarea' ? 'textarea' : 'contenteditable'),
        value: el.value || el.textContent,
        canEdit: el.getAttribute('parser-can-edit') === 'true',
        isFocused: el.getAttribute('parser-is-focused') === 'true'
      })),
    select_elements: Array.from(result.querySelectorAll('select[parser-semantic-id]'))
      .map(el => ({
        id: el.getAttribute('parser-semantic-id'),
        value: el.value,
        selectedIndex: el.selectedIndex,
        multiple: el.multiple,
        selectedValues: Array.from(el.selectedOptions).map(opt => opt.value)
      })),
  };
}

parse();
