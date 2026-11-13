"""What the landing screen measurably is - for the first-impression test and its guard.

The 5-second test judges a screenshot, and a model looking at a screenshot invents
geometry: in the first real audit of bnpparibas.pl all six first impressions called an
in-flow, white, 2 px-framed, 18 px notice a pop-up that "covers the screen" in "small grey
print", and a chat circle clipping about 10% of one button's edge an "almost completely
covered" button. This module measures the page instead (a script run in the live page
before anything scrolls), stores the numbers beside the screenshot, and turns them into
the short Polish block the pass is told to describe the screen from.

`capture_landing_facts` is called inside the browsing session and must never fail it or
hold it up. `backfill_landing_facts.py` runs the same script on a stored run.
"""
import asyncio
import logging
from typing import Any

from .mandated_notice import mandated_notice_reason, only_mandated
from .textutil import clip_words

log = logging.getLogger(__name__)

# Evaluated in the page. Notice candidates are (a) dialogs, (b) fixed/sticky elements with
# a z-index that take more than 2% of the viewport and carry text, (c) in-flow blocks in the
# top 45% of the page (and on the first screen) with 40-1500 characters of text and a
# close-like control. What a candidate covers is read from document.elementsFromPoint at the
# centres of the visible navigation links and buttons: a link counts as covered only when the
# topmost element there is inside the candidate and the link is further down the stack.
LANDING_FACTS_JS = r"""
() => {
  const t0 = performance.now();
  const BUDGET_MS = 2500;
  const late = () => performance.now() - t0 > BUDGET_MS;
  const result = { viewport: {}, notices: [], floating_widgets: [], ctas: [], nav_labels: [],
                   top_controls: [], truncated: false };
  try {
    const body = document.body, docEl = document.documentElement;
    const vw = window.innerWidth, vh = window.innerHeight;
    result.viewport = { width: vw, height: vh, dpr: window.devicePixelRatio || 1 };
    if (!body) return result;
    const vpArea = vw * vh;
    const docH = Math.max(docEl.scrollHeight, body.scrollHeight, vh);
    const sy = window.scrollY || 0;
    const topLimit = Math.min(0.45 * docH, vh);
    const r1 = (n) => Math.round(n * 10) / 10;
    const r3 = (n) => Math.round(n * 1000) / 1000;
    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const style = (el) => getComputedStyle(el);
    const parts = (c) => {
      const m = /^rgba?\(([^)]+)\)$/.exec(c || '');
      return m ? m[1].split(/[ ,\/]+/).filter(Boolean).map(parseFloat) : null;
    };
    const alphaOf = (c) => { const p = parts(c); return p ? (p.length > 3 ? p[3] : 1) : (c === 'transparent' ? 0 : 1); };
    const toHex = (c) => {
      const p = parts(c);
      if (!p) return c || '';
      const h = (n) => ('0' + Math.round(n).toString(16)).slice(-2);
      const hex = '#' + h(p[0]) + h(p[1]) + h(p[2]);
      return alphaOf(c) < 1 ? hex + ' (alfa ' + r3(alphaOf(c)) + ')' : hex;
    };
    const opacityChain = (el) => {
      let o = 1;
      for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(style(e).opacity);
      return o;
    };
    const isVisible = (el) => {
      const st = style(el);
      if (st.display === 'none' || st.visibility === 'hidden' || st.visibility === 'collapse') return false;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return false;
      return opacityChain(el) >= 0.05;
    };
    const clipped = (r) => {
      const w = Math.max(0, Math.min(vw, r.right) - Math.max(0, r.left));
      const h = Math.max(0, Math.min(vh, r.bottom) - Math.max(0, r.top));
      return { w, h, area: w * h };
    };
    const textOf = (el) => norm(el.innerText || el.textContent);
    const labelOf = (el) => {
      const img = el.querySelector ? el.querySelector('img[alt]') : null;
      return norm(el.innerText || el.textContent) || norm(el.getAttribute('aria-label')) ||
             norm(el.getAttribute('title')) || norm(el.getAttribute('alt')) ||
             norm(el.value) || (img ? norm(img.getAttribute('alt')) : '');
    };
    const rectOut = (r) => ({ x: r1(r.left), y: r1(r.top), w: r1(r.width), h: r1(r.height) });
    const stackAt = (x, y) => (document.elementsFromPoint ? document.elementsFromPoint(x, y)
                                                          : [document.elementFromPoint(x, y)]).filter(Boolean);
    const hasFixedAncestor = (el) => {
      for (let e = el.parentElement; e && e !== docEl; e = e.parentElement) if (style(e).position === 'fixed') return true;
      return false;
    };
    // A fixed wrapper with no size of its own (a chat launcher whose only child is the fixed
    // bubble) does not make the bubble part of anything: only a visible fixed ancestor does.
    const hasSizedFixedAncestor = (el) => {
      for (let e = el.parentElement; e && e !== docEl; e = e.parentElement) {
        if (style(e).position !== 'fixed') continue;
        const q = e.getBoundingClientRect();
        if (q.width >= 1 && q.height >= 1) return true;
      }
      return false;
    };
    const centre = (r) => {
      const c = clipped(r);
      return c.area > 0 ? { x: Math.max(0, r.left) + c.w / 2, y: Math.max(0, r.top) + c.h / 2 } : null;
    };
    const dedupeBy = (items, keyOf) => {
      const seen = new Set();
      return items.filter((i) => { const k = keyOf(i); if (seen.has(k)) return false; seen.add(k); return true; });
    };

    // --- primary navigation links and buttons (what an overlay could cover) ---
    const navSel = 'nav a, nav [role="menuitem"], [role="navigation"] a, [role="menubar"] [role="menuitem"], ' +
                   'header a, header [role="menuitem"], [class*="nav" i] a, [class*="menu" i] a';
    const ctaSel = 'button, [role="button"], input[type="submit"], input[type="button"], ' +
                   'a[class*="btn" i], a[class*="button" i], a[class*="cta" i]';
    const named = (list, filter) => {
      const out = [];
      for (const el of list) {
        if (late()) { result.truncated = true; break; }
        if (!isVisible(el)) continue;
        const label = labelOf(el);
        if (label.length < 2 || label.length > 40) continue;
        const r = el.getBoundingClientRect();
        const c = centre(r);
        if (!c || !filter(el, r)) continue;
        out.push({ el, label, r, cx: c.x, cy: c.y });
      }
      return dedupeBy(out, (i) => [i.label, Math.round(i.r.left), Math.round(i.r.top)].join('|'));
    };
    const navItems = named(document.querySelectorAll(navSel), () => true).slice(0, 60);
    const ctaItems = named(document.querySelectorAll(ctaSel), (el, r) => r.width >= 60 && r.height >= 28).slice(0, 30);

    // --- notice candidates ---
    const found = new Map();
    const add = (el, src) => { if (!found.has(el)) found.set(el, []); found.get(el).push(src); };
    document.querySelectorAll('[role="dialog"], [role="alertdialog"], [aria-modal="true"], dialog[open]').forEach((el) => {
      if (el.getAttribute('aria-hidden') === 'true' || !isVisible(el)) return;
      if (clipped(el.getBoundingClientRect()).area > 0 && textOf(el).length >= 10) add(el, 'dialog');
    });
    // Fixed and sticky elements, in open shadow roots too: chat widgets are often web components.
    const fixedEls = [], roots = [body];
    let scanned = 0;
    for (let ri = 0; ri < roots.length && ri < 40; ri++) {
      for (const el of roots[ri].querySelectorAll('*')) {
        if (++scanned > 12000 || late()) { result.truncated = true; break; }
        if (el.shadowRoot) roots.push(el.shadowRoot);
        const tag = el.tagName;
        if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' || tag === 'LINK' || tag === 'META') continue;
        const pos = style(el).position;
        if (pos === 'fixed' || pos === 'sticky') fixedEls.push(el);
      }
    }
    const navCount = (el) => navItems.filter((n) => el.contains(n.el)).length;
    // A fixed or sticky site header is navigation, not a notice, whatever it is built from: a
    // header or nav element in it, or a row that is nothing but links.
    const NAV_SEL = 'header, nav, [role="banner"], [role="navigation"], [role="menubar"]';
    const MODAL_SEL = '[role="dialog"], [role="alertdialog"], [aria-modal="true"], dialog';
    const isNavigation = (el) => {
      if (el.matches(MODAL_SEL)) return false;
      if (el.matches(NAV_SEL) || el.querySelector(NAV_SEL) || navCount(el) > 0) return true;
      const links = Array.from(el.querySelectorAll('a, [role="menuitem"]'));
      const linkText = links.reduce((n, a) => n + textOf(a).length, 0);
      return links.length >= 2 && linkText >= 0.7 * textOf(el).length;
    };
    for (const el of fixedEls) {
      const st = style(el);
      if (st.zIndex === 'auto' || !isVisible(el)) continue;
      const c = clipped(el.getBoundingClientRect());
      if (c.area / vpArea <= 0.02 || textOf(el).length < 15 || isNavigation(el)) continue;
      add(el, 'fixed');
    }
    const closeText = /^(zamknij|close|dismiss|akceptuj\w*|ok|rozumiem|zgadzam się|[×xX✕✖✗])(\s*[×xX✕✖✗])?$/i;
    const closeLabel = (el) => {
      const own = norm(el.innerText || el.textContent);
      if (own && closeText.test(own)) return own;
      const aria = norm(el.getAttribute('aria-label')) || norm(el.getAttribute('title'));
      return aria && /zamknij|close|dismiss/i.test(aria) ? aria : '';
    };
    const closeOf = new Map();
    for (const ctl of document.querySelectorAll('button, a, [role="button"], input[type="button"]')) {
      if (late()) { result.truncated = true; break; }
      const label = closeLabel(ctl);
      if (!label || !isVisible(ctl)) continue;
      const cr = ctl.getBoundingClientRect();
      if (cr.top + sy > topLimit) continue;
      let el = ctl.parentElement, hops = 0, block = null;
      while (el && el !== body && el !== docEl && hops++ < 8) {
        const len = textOf(el).length;
        if (len >= 40) { if (len <= 1500) block = el; break; }
        el = el.parentElement;
      }
      if (block && isVisible(block) && block.getBoundingClientRect().top + sy <= topLimit) {
        add(block, 'in_flow');
        if (!closeOf.has(block)) closeOf.set(block, label);
      }
    }
    let list = Array.from(found.keys());
    list = list.filter((el) => !list.some((o) => o !== el && o.contains(el)));

    const borderOf = (e) => {
      const s = style(e);
      for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
        const w = parseFloat(s['border' + side + 'Width']) || 0;
        if (w > 0 && s['border' + side + 'Style'] !== 'none') return { px: r1(w), color: toHex(s['border' + side + 'Color']) };
      }
      return null;
    };
    const effBackground = (el) => {
      for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
        const c = style(e).backgroundColor;
        if (alphaOf(c) > 0.05) return toHex(c);
      }
      return '#ffffff';
    };
    const isContentEl = (n) => {
      if (n === docEl || n === body) return false;
      if (['A', 'BUTTON', 'INPUT', 'IMG', 'VIDEO', 'SELECT', 'TEXTAREA', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'P',
           'LI', 'LABEL', 'SVG', 'CANVAS', 'PICTURE'].includes(n.tagName.toUpperCase())) return true;
      for (const ch of n.childNodes) if (ch.nodeType === 3 && ch.textContent.trim().length > 1) return true;
      return false;
    };
    const coversContentAt = (cand, x, y) => {
      let seen = false;
      for (const n of stackAt(x, y)) {
        if (n === cand || cand.contains(n)) { seen = true; continue; }
        if (!seen || n.contains(cand)) continue;
        if (isContentEl(n)) return true;
      }
      return false;
    };
    const coveredItem = (cand, item) => {
      if (cand.contains(item.el)) return false;
      const stack = stackAt(item.cx, item.cy);
      if (!stack.length || !(stack[0] === cand || cand.contains(stack[0]))) return false;
      return stack.some((n) => n === item.el || item.el.contains(n));
    };

    for (const el of list.slice(0, 12)) {
      if (late()) { result.truncated = true; break; }
      const sources = found.get(el);
      const r = el.getBoundingClientRect();
      const st = style(el);
      const c = clipped(r);
      const full = textOf(el);
      const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      const bySize = new Map();
      let minSize = 1e9, n = 0, node;
      while ((node = tw.nextNode()) && n++ < 400) {
        const t = norm(node.textContent);
        const pe = node.parentElement;
        if (t.length < 2 || !pe) continue;
        const ps = style(pe);
        if (ps.display === 'none' || ps.visibility === 'hidden') continue;
        const fs = parseFloat(ps.fontSize);
        const key = r1(fs) + '|' + ps.color;
        const cur = bySize.get(key) || { fs, color: ps.color, len: 0 };
        cur.len += t.length;
        bySize.set(key, cur);
        minSize = Math.min(minSize, fs);
      }
      let dom = null;
      bySize.forEach((v) => { if (!dom || v.len > dom.len) dom = v; });
      let border = borderOf(el);
      if (!border) {
        let e = el.parentElement, hops = 0;
        while (e && hops++ < 4 && textOf(e).length <= full.length + 8) { border = borderOf(e); if (border) break; e = e.parentElement; }
      }
      // What it covers: the visible navigation links and buttons it sits on top of, and any
      // content element (text, link, image) under a grid of points inside it.
      const navCovered = navItems.filter((i) => coveredItem(el, i));
      const ctaCovered = ctaItems.filter((i) => coveredItem(el, i));
      let coversContent = navCovered.length > 0 || ctaCovered.length > 0;
      if (!coversContent && c.area > 0) {
        for (const fx of [0.2, 0.4, 0.6, 0.8]) for (const fy of [0.25, 0.5, 0.75]) {
          if (coversContent) break;
          coversContent = coversContentAt(el, Math.max(0, r.left) + c.w * fx, Math.max(0, r.top) + c.h * fy);
        }
      }
      const fixedAnc = hasFixedAncestor(el);
      const positioned = st.position === 'fixed' || st.position === 'absolute' || fixedAnc || sources.includes('dialog');
      const isOverlay = positioned && coversContent;
      const kind = isOverlay ? 'overlay' : ((st.position === 'fixed' || st.position === 'sticky' || fixedAnc) ? 'fixed_bar'
                   : (st.position === 'absolute' ? 'absolute' : 'in_flow'));
      result.notices.push({
        text: full.slice(0, 240), text_full: full.slice(0, 1500),
        tag: el.tagName.toLowerCase(), id: (el.id || '').slice(0, 80), class: (el.getAttribute('class') || '').slice(0, 80),
        rect: rectOut(r), doc_top: r1(r.top + sy), position: st.position, z_index: st.zIndex,
        viewport_share: r3(c.area / vpArea), viewport_height_share: r3(c.h / vh),
        font_size_px: dom ? r1(dom.fs) : r1(parseFloat(st.fontSize)), font_size_min_px: minSize < 1e8 ? r1(minSize) : null,
        color: dom ? toHex(dom.color) : toHex(st.color), background_color: effBackground(el),
        border_px: border ? border.px : 0, border_color: border ? border.color : '',
        kind, is_overlay: isOverlay, covers_content: coversContent,
        covers_navigation: navCovered.length > 0, covers_ctas: ctaCovered.length > 0,
        covered: navCovered.concat(ctaCovered).slice(0, 6).map((i) => i.label),
        has_close_control: closeOf.has(el) || !!Array.from(el.querySelectorAll('button, a, [role="button"]')).find((b) => closeLabel(b)),
        close_label: closeOf.get(el) || '', sources,
      });
    }
    result.notices.sort((a, b) => b.viewport_share - a.viewport_share);
    const noticeEls = list;
    const inNotice = (el) => noticeEls.some((o) => o.contains(el) || el.contains(o));

    // --- floating widgets: small fixed elements, and how much of each button something else sits on ---
    // The overlap is read from hit-testing a grid of points over the button (and over its label),
    // so it follows the real shape (a round chat bubble) and finds a widget however it is built
    // (a web component whose bubble lives in a closed shadow root answers as its host).
    const widgets = [];
    // Round when the element, or a child that fills it, has a radius of at least 45%: a chat
    // launcher is often a square wrapper around a round button.
    const radiusRound = (e) => {
      const q = e.getBoundingClientRect(), br = style(e).borderTopLeftRadius;
      return br.endsWith('%') ? parseFloat(br) >= 45 : parseFloat(br) >= 0.45 * Math.min(q.width, q.height);
    };
    const isRound = (el, r) => {
      if (radiusRound(el)) return true;
      let n = 0;
      for (const ch of el.querySelectorAll('*')) {
        if (n++ > 30) break;
        const q = ch.getBoundingClientRect();
        if (q.width >= 0.7 * r.width && q.height >= 0.7 * r.height && radiusRound(ch)) return true;
      }
      return false;
    };
    const widgetLabel = (el) => {
      const own = labelOf(el);
      if (own) return own;
      const d = el.querySelector('[aria-label], [title]');
      return d ? norm(d.getAttribute('aria-label') || d.getAttribute('title')) : '';
    };
    for (const el of fixedEls) {
      if (late()) { result.truncated = true; break; }
      if (widgets.length >= 6) break;
      const st = style(el);
      if (st.position !== 'fixed' || inNotice(el) || hasSizedFixedAncestor(el) || !isVisible(el)) continue;
      const r = el.getBoundingClientRect();
      const c = clipped(r);
      if (c.area <= 0 || c.area / vpArea > 0.08 || r.width < 12 || r.height < 12) continue;
      if (textOf(el).length >= 40 || navCount(el) >= 3) continue;
      widgets.push({ el, r, st, round: isRound(el, r), overlaps: [] });
    }
    const labelRect = (el) => {
      try { const g = document.createRange(); g.selectNodeContents(el); return g.getBoundingClientRect(); } catch (e) { return null; }
    };
    // What a hit stands for: the nearest floating ancestor-or-self or web-component host, else
    // the block just below the ancestor the hit and the button share.
    const layerOf = (top, ctaEl) => {
      let e = top;
      for (;;) {
        if (['fixed', 'sticky', 'absolute'].includes(style(e).position) || e.shadowRoot || e.tagName.includes('-')) return e;
        if (!e.parentElement || e.parentElement.contains(ctaEl)) return e;
        e = e.parentElement;
      }
    };
    const hostOf = (el) => { const r = el.getRootNode ? el.getRootNode() : null; return r && r.host ? r.host : null; };
    const same = (a, b) => a === b || a.contains(b) || b.contains(a) ||
      (hostOf(a) && (hostOf(a) === b || b.contains(hostOf(a)))) || (hostOf(b) && (hostOf(b) === a || a.contains(hostOf(b))));
    const COLS = 24, ROWS = 10;
    const gridHits = (cta, rect) => {
      const hits = new Map(), c = clipped(rect);
      if (c.area <= 0) return hits;
      for (let i = 0; i < COLS; i++) for (let j = 0; j < ROWS; j++) {
        const x = Math.max(0, rect.left) + c.w * (i + 0.5) / COLS, y = Math.max(0, rect.top) + c.h * (j + 0.5) / ROWS;
        const top = stackAt(x, y)[0];
        if (!top || top === cta.el || cta.el.contains(top) || top.contains(cta.el)) continue;
        const layer = layerOf(top, cta.el);
        hits.set(layer, (hits.get(layer) || 0) + 1);
      }
      return hits;
    };
    for (const cta of ctaItems) {
      if (late()) { result.truncated = true; break; }
      const onButton = gridHits(cta, cta.r);
      if (!onButton.size) continue;
      const lr = labelRect(cta.el);
      const onLabel = lr && lr.width > 0 && lr.height > 0 ? gridHits(cta, lr) : new Map();
      for (const [layer, n] of onButton) {
        if (inNotice(layer)) continue;
        let w = widgets.find((x) => same(x.el, layer));
        if (!w) {
          const r = layer.getBoundingClientRect(), st = style(layer);
          const area = clipped(r).area;
          const noBox = r.width < 1 || r.height < 1;
          const floats = ['fixed', 'absolute', 'sticky'].includes(st.position) || hasSizedFixedAncestor(layer) || noBox;
          if (area / vpArea > 0.08 || !floats || widgets.length >= 6) continue;
          w = { el: layer, r, st, round: false, overlaps: [] };
          widgets.push(w);
        }
        const l = onLabel.get(layer) || 0;
        w.overlaps.push({ cta: cta.label, overlap_share: r3(n / (COLS * ROWS)), label_overlapped: l > 0,
                          label_overlap_share: r3(l / (COLS * ROWS)) });
      }
    }
    for (const w of widgets) {
      const el = w.el, c = clipped(w.r);
      result.floating_widgets.push({
        tag: el.tagName.toLowerCase(), id: (el.id || '').slice(0, 80), class: (el.getAttribute('class') || '').slice(0, 80),
        label: widgetLabel(el).slice(0, 60), rect: rectOut(w.r), position: w.st.position, z_index: w.st.zIndex,
        shape: w.round ? 'round' : 'rect', viewport_share: r3(c.area / vpArea), overlaps: w.overlaps.slice(0, 6),
      });
    }

    // --- what is on the screen, so a suggested change can name something that exists ---
    result.nav_labels = dedupeBy(navItems.filter((i) => !inNotice(i.el)).map((i) => i.label), (l) => l).slice(0, 25);
    result.ctas = ctaItems.filter((i) => !inNotice(i.el)).slice(0, 15)
      .map((i) => ({ text: i.label, rect: rectOut(i.r) }));
    const shown = new Set(result.nav_labels.concat(result.ctas.map((c) => c.text)));
    const small = named(document.querySelectorAll('a, button, [role="button"]'), (el, r) => r.width <= 140 && r.height <= 100);
    result.top_controls = dedupeBy(small.filter((i) => !inNotice(i.el) && !shown.has(i.label)).map((i) => i.label), (l) => l)
      .slice(0, 15);
  } catch (e) {
    result.error = String(e).slice(0, 200);
  }
  return result;
}
"""

_MAX_TEXT = 300
_MAX_LIST = 40


def _cap(value: Any) -> Any:
    """`value` with every string and list bounded: the facts go into a prompt and a file."""
    if isinstance(value, str):
        return value[:_MAX_TEXT]
    if isinstance(value, list):
        return [_cap(v) for v in value[:_MAX_LIST]]
    if isinstance(value, dict):
        return {str(k)[:60]: _cap(v) for k, v in value.items()}
    return value


def _without_close_label(text: str, close_label: Any) -> str:
    """The notice's text without the "Zamknij ×" the close button puts in front of it."""
    label = str(close_label or "").strip()
    return text[len(label):].strip() if label and text.startswith(label) else text.strip()


def finalize_facts(raw: Any) -> dict | None:
    """The script's output as it is stored: bounded, with the mandated verdict per notice.

    The verdict is decided from the notice's whole text (the script returns up to 1500
    characters of it) and only the 240-character excerpt is kept. None for anything that is
    not a measurement.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("viewport"), dict) or "error" in raw and not raw.get("notices"):
        return None
    facts = _cap({k: v for k, v in raw.items() if k not in ("notices",)})
    notices = []
    for n in (raw.get("notices") or [])[:6]:
        if not isinstance(n, dict):
            continue
        full = _without_close_label(str(n.get("text_full") or n.get("text") or ""), n.get("close_label"))
        notice = _cap({k: v for k, v in n.items() if k not in ("text_full", "text")})
        notice["text"] = full[:240]
        reason = mandated_notice_reason(full)
        notice["mandated"] = reason is not None
        notice["mandated_reason"] = reason or ""
        notices.append(notice)
    facts["notices"] = notices
    facts["version"] = 1
    return facts


async def capture_landing_facts(page, store, timeout: float = 8.0) -> dict | None:
    """Measure the landing screen and store landing_facts.json; None when it could not be.

    Called in the middle of a browsing session: a failure, a hang or an odd page costs this
    file and nothing else.
    """
    try:
        raw = await asyncio.wait_for(page.evaluate(LANDING_FACTS_JS), timeout)
        facts = finalize_facts(raw)
        if facts is None:
            log.warning("landing facts: the page returned no measurement")
            return None
        store.write_landing_facts(facts)
        return facts
    except Exception as exc:  # noqa: BLE001 - a missing measurement must not end the session
        log.warning("landing facts failed: %s", exc)
        return None


# --- the block the first-impression pass reads ----------------------------------

NO_FACTS = ("(brak pomiarów strony startowej: nie opisuj geometrii, kolorów ani rozmiaru czcionki żadnego "
            "elementu - oceniaj tylko to, co widać i da się nazwać, np. tekst elementu i to, że jest u góry)")


# The script finds dialogs, fixed bars and blocks with a close control; a notice without one
# in the flow of the page, or in an iframe, is not seen, so "none found" is not "none there".
NO_NOTICE_DETECTED = ("- Pomiar nie wykrył komunikatu z przyciskiem zamknięcia, okna dialogowego ani przyklejonego "
                      "paska; nie oznacza to, że na zrzucie ekranu nie ma komunikatu - opisz taki element "
                      "tylko tym, co widać (tekst, miejsce), bez podawania jego wymiarów, kolorów i rozmiaru czcionki.")


def _pct(share: Any) -> str:
    try:
        return f"{round(float(share) * 100)}%"
    except (TypeError, ValueError):
        return "?"


def _px(value: Any) -> str:
    try:
        v = float(value)
        return f"{int(v) if v == int(v) else round(v, 1)} px"
    except (TypeError, ValueError):
        return "?"


def _notice_line(n: dict) -> str:
    title = clip_words(" ".join(str(n.get("text") or "").split()), 90)
    kind = n.get("kind")
    if kind == "overlay":
        where = "nakładka na treść strony (okno nad innymi elementami)"
    elif kind == "fixed_bar":
        where = (f"pasek przyklejony do krawędzi ekranu (position: {n.get('position')}), "
                 "który niczego nie zasłania")
    elif kind == "absolute":
        where = "blok pozycjonowany bezwzględnie (position: absolute), który niczego nie zasłania"
    else:
        where = "blok w przepływie strony (NIE jest oknem, pop-upem ani nakładką; przesuwa treść w dół, niczego nie zasłania)"
    parts = [f"- Komunikat „{title}”: {where}",
             f"zajmuje {_pct(n.get('viewport_share'))} powierzchni pierwszego ekranu "
             f"({_pct(n.get('viewport_height_share'))} jego wysokości)",
             f"tekst {_px(n.get('font_size_px'))} w kolorze {n.get('color') or '?'}, tło {n.get('background_color') or '?'}"]
    border = n.get("border_px")
    parts.append(f"ramka {_px(border)} {n.get('border_color') or ''}".strip() if border else "bez ramki")
    if n.get("covers_navigation") or n.get("covers_ctas"):
        covered = ", ".join(f"„{c}”" for c in n.get("covered") or [])
        parts.append(f"ZASŁANIA: {covered}" if covered else "ZASŁANIA nawigację lub przyciski")
    elif n.get("covers_content"):
        parts.append("leży nad innymi elementami treści, ale nie zasłania menu ani przycisków")
    else:
        parts.append("nie zasłania nawigacji ani przycisków")
    if n.get("has_close_control"):
        label = f" („{n['close_label']}”)" if n.get("close_label") else ""
        parts.append(f"ma przycisk zamknięcia{label}")
    else:
        parts.append("nie ma przycisku zamknięcia")
    line = "; ".join(parts) + "."
    if n.get("mandated"):
        line += (f" Komunikat OBOWIĄZKOWY, powód: {n.get('mandated_reason') or 'narzucony przez organ lub przepis'}. "
                 "Jego treść, ramka i obecność są narzucone - nie wolno zalecać ich ukrycia, zmniejszenia, "
                 "złagodzenia ani usunięcia, tylko poprawę czytelności.")
    return line


def _widget_line(w: dict) -> str:
    name = w.get("label") or w.get("id") or w.get("class") or w.get("tag") or "element"
    size = w.get("rect") or {}
    shape = "okrągły" if w.get("shape") == "round" else "prostokątny"
    dims = (f"{shape}, {_px(size.get('w'))}×{_px(size.get('h'))}, {_pct(w.get('viewport_share'))} ekranu"
            if size.get("w") else "rozmiaru nie da się podać")
    head = f"- Pływający element „{name}” ({dims})"
    overlaps = [o for o in w.get("overlaps") or [] if isinstance(o, dict)]
    if not overlaps:
        return head + " nie nachodzi na żaden widoczny przycisk."
    bits = []
    for o in overlaps:
        label = ("napis przycisku jest zasłonięty" if o.get("label_overlapped")
                 else "napis przycisku jest w całości widoczny")
        bits.append(f"nachodzi na przycisk „{o.get('cta')}” w {_pct(o.get('overlap_share'))} jego powierzchni "
                    f"({label})")
    return head + " " + "; ".join(bits) + ". Kilka procent to drobne nałożenie, nie zasłonięcie przycisku."


def summarize_landing_facts(facts: dict | None) -> str:
    """The measured facts as the Polish block for the first-impression prompt; the
    "no measurements" sentence when the file is absent or empty."""
    if not isinstance(facts, dict) or not isinstance(facts.get("viewport"), dict):
        return NO_FACTS
    vp = facts["viewport"]
    lines = [f"Pomiar w przeglądarce, ekran {_px(vp.get('width'))}×{_px(vp.get('height'))}, przed jakąkolwiek "
             "akcją (jedyne wiarygodne źródło geometrii, kolorów i rozmiarów):"]
    notices = [n for n in facts.get("notices") or [] if isinstance(n, dict)]
    lines += [_notice_line(n) for n in notices] or [NO_NOTICE_DETECTED]
    widgets = [w for w in facts.get("floating_widgets") or [] if isinstance(w, dict)]
    lines += [_widget_line(w) for w in widgets] or ["- Nie wykryto pływających elementów (czat, przycisk „do góry”)."]
    if facts.get("nav_labels"):
        lines.append("- Widoczne pozycje menu: " + "; ".join(str(x) for x in facts["nav_labels"][:25]) + ".")
    ctas = [c.get("text") for c in facts.get("ctas") or [] if isinstance(c, dict) and c.get("text")]
    if ctas:
        lines.append("- Widoczne przyciski: " + "; ".join(str(x) for x in ctas[:15]) + ".")
    if facts.get("top_controls"):
        lines.append("- Inne małe kontrolki (ikony, linki): " + "; ".join(str(x) for x in facts["top_controls"][:15]) + ".")
    if facts.get("truncated"):
        lines.append("- Pomiar został przerwany limitem czasu: lista może być niepełna.")
    return "\n".join(lines)


def landing_notice_rollup(stores: list) -> dict:
    """What the landing measurements of a run say about notices, for the aggregate guard
    (statistics.landing_notices). Empty when no session was measured, so a run stored
    before landing facts keeps its statistics unchanged."""
    measured, notices, left_out = 0, {}, 0
    for store in stores:
        try:
            facts = store.read_landing_facts()
            fi = store.read_first_impression()
        except Exception:  # noqa: BLE001
            continue
        if isinstance(fi, dict) and fi.get("mandated_notice_left_out"):
            left_out += 1
        if not isinstance(facts, dict):
            continue
        measured += 1
        for n in facts.get("notices") or []:
            if not isinstance(n, dict):
                continue
            key = " ".join(str(n.get("text") or "").lower().split())[:80]
            entry = notices.setdefault(key, {"title": " ".join(str(n.get("text") or "").split())[:100],
                                             "mandated": n.get("mandated") is True,
                                             "kind": n.get("kind"), "sessions": 0})
            entry["sessions"] += 1
    if not measured:
        return {"left_out": left_out} if left_out else {}
    listed = list(notices.values())
    return {"measured_sessions": measured, "notices": listed, "left_out": left_out,
            "mandated": any(n["mandated"] for n in listed),
            "only_mandated": only_mandated({"notices": listed})}
