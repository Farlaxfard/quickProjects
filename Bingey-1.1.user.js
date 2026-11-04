// ==UserScript==
// @name         Bingey
// @namespace    local.pixelshield
// @version      1.1
// @description  Pixelates thumbnails and covers titles with skeleton bars per card until hovered. Alt+B toggles binge mode. Delays YouTube's hover preview by 8s.
// @match        https://www.youtube.com/*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(() => {
  'use strict';

  /* ---------- knobs ---------- */
  const HOTKEY_CODE = 'KeyB';          // Alt + B (physical key, so Mac Option+B works too)
  const BLOCKS = [22, 11, 5];          // thumbnail pixel-block sizes in px, coarse -> fine
  const PREVIEW_DELAY_MS = 3000;       // hover time before YouTube's inline preview starts
  const STORE_KEY = 'pixelshield-binge';

  /* ---------- selectors ---------- */
  const CARD = [
    'yt-lockup-view-model',
    'ytm-shorts-lockup-view-model',
    'ytm-shorts-lockup-view-model-v2',
    'ytd-rich-grid-media',
    'ytd-compact-video-renderer',
    'ytd-reel-item-renderer',
    'ytd-rich-item-renderer',
  ].join(',');
  const SKIP = '#movie_player, ytd-player, .html5-video-player, ytd-miniplayer';
  const THUMB = 'ytd-thumbnail, yt-thumbnail-view-model, a#thumbnail';

  const root = document.documentElement;

  /* ---------- CSS ---------- */
  const css = `
    /* thumbnail pixel layers */
    .px-cv{
      display:none; position:absolute; inset:0; width:100%; height:100%;
      image-rendering:pixelated; pointer-events:none; border-radius:12px;
      opacity:1; transition:opacity .3s ease; will-change:opacity;
    }
    html[data-px-scope] .px-cv{ display:block; }

    /* hover (per card) or binge mode: fade layers out coarse -> fine, staggered */
    html[data-px-scope] [data-px-card]:hover .px-cv,
    html[data-px-binge] .px-cv{ opacity:0; }
    html[data-px-scope] [data-px-card]:hover .px-cv[data-l="1"],
    html[data-px-binge] .px-cv[data-l="1"]{ transition-delay:.12s; }
    html[data-px-scope] [data-px-card]:hover .px-cv[data-l="2"],
    html[data-px-binge] .px-cv[data-l="2"]{ transition-delay:.24s; }

    /* titles: opaque skeleton placeholder (two bars) over the title, faded out on hover */
    html[data-px-scope] [data-px-card] h3{ position:relative; }
    html[data-px-scope] [data-px-card] h3::before{
      content:""; position:absolute; inset:-2px -1px; z-index:2; pointer-events:none;
      border-radius:6px; opacity:1; transition:opacity .3s ease;
      --px-bar: var(--yt-spec-10-percent-layer, rgba(128,128,128,.28));
      background:
        linear-gradient(var(--px-bar), var(--px-bar)) 0 3px / 100% 13px no-repeat,
        linear-gradient(var(--px-bar), var(--px-bar)) 0 23px / 62% 13px no-repeat,
        var(--yt-spec-base-background, #0f0f0f);
    }
    html[data-px-scope] [data-px-card]:hover h3::before,
    html[data-px-binge] [data-px-card] h3::before{ opacity:0; }

    .px-toast{
      position:fixed; left:50%; bottom:28px; transform:translateX(-50%) translateY(10px);
      background:rgba(20,20,20,.92); color:#fff; font:600 14px/1 Roboto,Arial,sans-serif;
      padding:12px 18px; border-radius:999px; z-index:2147483647; opacity:0;
      pointer-events:none; transition:opacity .2s, transform .2s;
    }
    .px-toast.show{ opacity:1; transform:translateX(-50%) translateY(0); }
  `;
  const style = document.createElement('style');
  style.textContent = css;
  root.appendChild(style);

  /* ---------- thumbnail pixelation (tiny canvases scaled up with image-rendering:pixelated) ---------- */
  // Halve repeatedly so big downscales average properly instead of aliasing.
  function shrink(src, sx, sy, sw, sh, tw, th) {
    let cur = src, cx = sx, cy = sy, cw = sw, ch = sh;
    while (cw / 2 > tw && ch / 2 > th) {
      const t = document.createElement('canvas');
      t.width = Math.ceil(cw / 2);
      t.height = Math.ceil(ch / 2);
      t.getContext('2d').drawImage(cur, cx, cy, cw, ch, 0, 0, t.width, t.height);
      cur = t; cx = 0; cy = 0; cw = t.width; ch = t.height;
    }
    return [cur, cx, cy, cw, ch];
  }

  function draw(img) {
    const w = img.naturalWidth, h = img.naturalHeight;
    if (!w || !h || !img._pxc) return;
    const dw = img.clientWidth || 320, dh = img.clientHeight || 180;
    BLOCKS.forEach((b, i) => {
      const cols = Math.max(2, Math.round(dw / b));
      const rows = Math.max(2, Math.round(dh / b));
      const c = img._pxc[i];
      c.width = cols; c.height = rows;
      // emulate object-fit: cover so pixels line up with what's visible
      const ar = cols / rows;
      let sw = w, sh = h;
      if (w / h > ar) sw = h * ar; else sh = w / ar;
      const sx = (w - sw) / 2, sy = (h - sh) / 2;
      const ctx = c.getContext('2d');
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      const [s, a, bb, cw, ch] = shrink(img, sx, sy, sw, sh, cols, rows);
      ctx.drawImage(s, a, bb, cw, ch, 0, 0, cols, rows);
    });
  }

  function attach(img) {
    const parent = img.parentElement;
    if (!parent) return;
    if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';
    const cs = BLOCKS.map((_, i) => {
      const c = document.createElement('canvas');
      c.className = 'px-cv';
      c.dataset.l = String(i);
      return c;
    });
    img._pxc = cs;
    img.after(...cs.slice().reverse()); // finest first, coarsest last = on top
    if (!img._pxl) {
      img._pxl = true;
      img.addEventListener('load', () => draw(img)); // YouTube recycles <img>s, redraw on new src
    }
    if (img.complete && img.naturalWidth) draw(img);
  }

  /* ---------- scope + scanning ---------- */
  function updateScope() {
    const p = location.pathname;
    root.toggleAttribute('data-px-scope', p === '/' || p.startsWith('/watch'));
  }

  function scan() {
    updateScope();
    for (const img of document.querySelectorAll('img[src*="ytimg.com/vi"]')) {
      if (img._pxc && img._pxc[0].isConnected) continue;
      if (img.closest(SKIP)) continue;
      const card = img.closest(CARD);
      if (!card) continue;
      card.setAttribute('data-px-card', '');
      attach(img);
    }
  }

  let queued = false;
  const schedule = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; scan(); });
  };

  /* ---------- hover-preview delay ----------
     YouTube starts its inline preview from mouseover/mouseenter on the thumbnail.
     We swallow those in the capture phase, wait PREVIEW_DELAY_MS of continuous hover,
     then replay them so YouTube starts the preview as normal. */
  const pending = new Map();   // thumbnail -> timer id
  const released = new WeakSet();
  let mx = 0, my = 0;

  window.addEventListener('mousemove', (e) => { mx = e.clientX; my = e.clientY; }, true);

  function thumbOf(e) {
    const t = e.target;
    if (!(t instanceof Element)) return null;
    if (t.closest(SKIP)) return null;
    return t.closest(THUMB);
  }

  function onEnter(e) {
    const th = thumbOf(e);
    if (!th || released.has(th) || !e.isTrusted) return; // replayed (untrusted) events pass through
    e.stopImmediatePropagation();
    if (pending.has(th)) return;
    pending.set(th, setTimeout(() => {
      pending.delete(th);
      if (!th.isConnected || !th.matches(':hover')) return;
      released.add(th);
      const init = { bubbles: true, cancelable: true, view: window, clientX: mx, clientY: my };
      th.dispatchEvent(new MouseEvent('mouseover', init));
      th.dispatchEvent(new MouseEvent('mouseenter', { ...init, bubbles: false }));
    }, PREVIEW_DELAY_MS));
  }
  window.addEventListener('mouseover', onEnter, true);
  window.addEventListener('mouseenter', onEnter, true);

  window.addEventListener('mouseout', (e) => {
    const th = thumbOf(e);
    if (!th) return;
    if (e.relatedTarget instanceof Node && th.contains(e.relatedTarget)) return;
    clearTimeout(pending.get(th));
    pending.delete(th);
    released.delete(th);
  }, true);

  /* ---------- binge mode ---------- */
  let toast;
  function say(text) {
    if (!toast) {
      toast = document.createElement('div');
      toast.className = 'px-toast';
      root.appendChild(toast);
    }
    toast.textContent = text;
    toast.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => toast.classList.remove('show'), 1400);
  }

  function setBinge(on, announce) {
    root.toggleAttribute('data-px-binge', on);
    try { localStorage.setItem(STORE_KEY, on ? '1' : '0'); } catch (_) {}
    if (announce) say(on ? 'Binge mode ON \u{1F37F}' : 'Binge mode OFF \u{1F512}');
  }

  window.addEventListener('keydown', (e) => {
    if (e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.code === HOTKEY_CODE) {
      e.preventDefault();
      e.stopPropagation();
      setBinge(!root.hasAttribute('data-px-binge'), true);
    }
  }, true);

  /* ---------- boot ---------- */
  try { if (localStorage.getItem(STORE_KEY) === '1') root.setAttribute('data-px-binge', ''); } catch (_) {}
  updateScope();

  const start = () => {
    new MutationObserver(schedule).observe(root, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['src'],
    });
    window.addEventListener('yt-navigate-finish', schedule);
    scan();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();