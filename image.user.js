// ==UserScript==
// @name         Image Agent
// @namespace    local.image.downloader
// @version      1.0.0
// @description  Right-click an image to download it as Original/PNG/JPEG/WebP/BMP with no save dialog. Optional hover download buttons on images.
// @match        *://*/*
// @grant        GM_download
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      *
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  // ---------------------------------------------------------------- config
  const FORMATS = [
    { id: 'original', label: 'Original format' },
    { id: 'png',  label: 'PNG',  mime: 'image/png' },
    { id: 'jpg',  label: 'JPEG', mime: 'image/jpeg', quality: 0.92, flatten: true },
    { id: 'webp', label: 'WebP', mime: 'image/webp', quality: 0.92 },
    { id: 'bmp',  label: 'BMP',  flatten: true } // hand-encoded, browsers can't export BMP natively
  ];
  const MIN_HOVER_SIZE = 40; // px; skip icons/tracking pixels for hover buttons
  const STORE_KEY = 'hoverButtons';

  const isHoverOn = () => !!GM_getValue(STORE_KEY, false);

  // ---------------------------------------------------------------- helpers
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const k in attrs || {}) {
      if (k === 'class') el.className = attrs[k];
      else if (k === 'text') el.textContent = attrs[k];
      else el.setAttribute(k, attrs[k]);
    }
    kids.forEach((c) => el.append(c));
    return el;
  }

  function icon(d) {
    const NS = 'http://www.w3.org/2000/svg';
    const s = document.createElementNS(NS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('width', '16');
    s.setAttribute('height', '16');
    const p = document.createElementNS(NS, 'path');
    p.setAttribute('d', d);
    p.setAttribute('fill', 'none');
    p.setAttribute('stroke', 'currentColor');
    p.setAttribute('stroke-width', '2.2');
    p.setAttribute('stroke-linecap', 'round');
    p.setAttribute('stroke-linejoin', 'round');
    s.appendChild(p);
    return s;
  }

  const MIME_EXT = {
    'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png', 'image/gif': 'gif',
    'image/webp': 'webp', 'image/avif': 'avif', 'image/svg+xml': 'svg', 'image/bmp': 'bmp',
    'image/x-icon': 'ico', 'image/vnd.microsoft.icon': 'ico', 'image/tiff': 'tiff'
  };
  const EXT_MIME = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', gif: 'image/gif',
    webp: 'image/webp', avif: 'image/avif', svg: 'image/svg+xml', bmp: 'image/bmp', ico: 'image/x-icon' };

  function extFromUrl(src) {
    try {
      if (/^(data|blob):/i.test(src)) return '';
      const m = new URL(src, location.href).pathname.match(/\.([a-z0-9]{2,5})$/i);
      return m ? m[1].toLowerCase().replace('jpeg', 'jpg') : '';
    } catch (e) { return ''; }
  }

  function baseName(src) {
    let n = 'image';
    try {
      if (!/^(data|blob):/i.test(src)) {
        const u = new URL(src, location.href);
        const seg = decodeURIComponent(u.pathname.split('/').filter(Boolean).pop() || '');
        n = seg.replace(/\.[a-z0-9]{2,5}$/i, '') || u.hostname;
      }
    } catch (e) { /* keep default */ }
    n = n.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '_').trim().slice(0, 100);
    return n || 'image';
  }

  // ---------------------------------------------------------------- fetch / convert / save
  function fetchBlob(url) {
    if (/^(data|blob):/i.test(url)) return fetch(url).then((r) => r.blob());
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'GET',
        url,
        responseType: 'blob',
        headers: { Referer: location.href },
        timeout: 60000,
        onload: (r) =>
          r.status >= 200 && r.status < 300 && r.response
            ? resolve(r.response)
            : reject(new Error('HTTP ' + r.status)),
        onerror: () => reject(new Error('network error')),
        ontimeout: () => reject(new Error('timed out'))
      });
    });
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error('could not decode image'));
      im.src = url;
    });
  }

  function encodeBMP(id) {
    const w = id.width, hgt = id.height;
    const rowSize = (w * 3 + 3) & ~3;
    const size = 54 + rowSize * hgt;
    const buf = new ArrayBuffer(size);
    const dv = new DataView(buf);
    const u8 = new Uint8Array(buf);
    dv.setUint16(0, 0x4d42, true);
    dv.setUint32(2, size, true);
    dv.setUint32(10, 54, true);
    dv.setUint32(14, 40, true);
    dv.setInt32(18, w, true);
    dv.setInt32(22, hgt, true);
    dv.setUint16(26, 1, true);
    dv.setUint16(28, 24, true);
    dv.setUint32(34, rowSize * hgt, true);
    dv.setInt32(38, 2835, true);
    dv.setInt32(42, 2835, true);
    const d = id.data;
    for (let y = 0; y < hgt; y++) {
      let p = 54 + (hgt - 1 - y) * rowSize;
      let s = y * w * 4;
      for (let x = 0; x < w; x++) {
        u8[p++] = d[s + 2];
        u8[p++] = d[s + 1];
        u8[p++] = d[s];
        s += 4;
      }
    }
    return new Blob([buf], { type: 'image/bmp' });
  }

  async function convert(blob, fmt, imgEl, src) {
    // make sure the decoder knows what it's looking at (esp. SVG served without a type)
    if (!/^image\//i.test(blob.type)) {
      const guess = EXT_MIME[extFromUrl(src)];
      if (guess) blob = new Blob([blob], { type: guess });
    }
    const url = URL.createObjectURL(blob);
    try {
      const im = await loadImage(url);
      let w = im.naturalWidth, hgt = im.naturalHeight;
      if (!w || !hgt) {
        const r = imgEl.getBoundingClientRect();
        w = Math.round(r.width) || 1024;
        hgt = Math.round(r.height) || 1024;
      }
      const c = document.createElement('canvas');
      c.width = w;
      c.height = hgt;
      const ctx = c.getContext('2d');
      if (fmt.flatten) {
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, w, hgt);
      }
      ctx.drawImage(im, 0, 0, w, hgt);
      if (fmt.id === 'bmp') return encodeBMP(ctx.getImageData(0, 0, w, hgt));
      const out = await new Promise((res) => c.toBlob(res, fmt.mime, fmt.quality));
      if (!out) throw new Error('export failed (image too large?)');
      if (out.type !== fmt.mime) throw new Error(fmt.label + ' encoding not supported by this browser');
      return out;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  function save(blob, name) {
    const url = URL.createObjectURL(blob);
    const revoke = () => setTimeout(() => URL.revokeObjectURL(url), 60000);
    let fellBack = false;
    const fallback = () => {
      if (fellBack) return;
      fellBack = true;
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      a.style.display = 'none';
      (document.body || document.documentElement).appendChild(a);
      a.click();
      a.remove();
      revoke();
    };
    if (typeof GM_download === 'function') {
      try {
        GM_download({ url, name, saveAs: false, onload: revoke, onerror: fallback, ontimeout: fallback });
      } catch (e) {
        fallback();
      }
    } else {
      fallback();
    }
  }

  async function download(src, imgEl, fmtId) {
    const fmt = FORMATS.find((f) => f.id === fmtId);
    toast('Preparing ' + fmt.label + '…', 0);
    try {
      const blob = await fetchBlob(src);
      const base = baseName(src);
      if (fmt.id === 'original') {
        const ext = MIME_EXT[(blob.type || '').toLowerCase()] || extFromUrl(src) || 'img';
        save(blob, base + '.' + ext);
      } else {
        const out = await convert(blob, fmt, imgEl, src);
        save(out, base + '.' + fmt.id);
      }
      toast('Downloading as ' + fmt.label);
    } catch (err) {
      toast('Download failed: ' + (err && err.message ? err.message : err), 4500, true);
    }
  }

  // ---------------------------------------------------------------- UI (shadow DOM so page CSS can't touch it)
  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; font: 13px/1.2 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .menu, .sub { background: #1f2023; color: #eceef1; border: 1px solid #3a3c42; border-radius: 9px;
      box-shadow: 0 8px 28px rgba(0,0,0,.45); padding: 4px; user-select: none; }
    .menu { position: fixed; min-width: 220px; }
    .menu[hidden], .bar[hidden], .toast[hidden] { display: none; }
    .item { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: 6px; cursor: default; }
    .item:hover, .has-sub:hover > .item { background: #34373d; }
    .lbl { flex: 1; white-space: nowrap; }
    .r { opacity: .6; }
    .sep { height: 1px; background: #3a3c42; margin: 4px 2px; }
    .hint { padding: 4px 10px 3px; font-size: 11px; opacity: .45; }
    .has-sub { position: relative; }
    .sub { display: none; position: absolute; left: calc(100% - 4px); top: -5px; min-width: 170px; }
    .has-sub:hover > .sub { display: block; }
    .menu.flip .sub { left: auto; right: calc(100% - 4px); }
    .menu.up .sub { top: auto; bottom: -5px; }
    .sw { width: 28px; height: 16px; border-radius: 9px; background: #555961; position: relative; flex: none; }
    .sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px;
      border-radius: 50%; background: #fff; transition: left .12s; }
    .sw.on { background: #3b82f6; }
    .sw.on::after { left: 14px; }
    .bar { position: fixed; display: flex; gap: 2px; padding: 3px; background: rgba(24,25,28,.88);
      border-radius: 9px; box-shadow: 0 2px 10px rgba(0,0,0,.4); backdrop-filter: blur(4px); }
    .bar button { all: unset; display: grid; place-items: center; width: 26px; height: 24px;
      border-radius: 6px; color: #fff; cursor: pointer; }
    .bar button:hover { background: rgba(255,255,255,.22); }
    .toast { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%); background: #1f2023;
      color: #eceef1; border: 1px solid #3a3c42; padding: 9px 14px; border-radius: 9px;
      box-shadow: 0 6px 20px rgba(0,0,0,.4); pointer-events: none; }
    .toast.err { background: #5a1f24; border-color: #8a3038; }
  `;

  let host, menuEl, barEl, toastEl, toastT;
  let hoverImg = null, hideT = null, menuOpen = false;

  function ensureUI() {
    if (host && host.isConnected) return;
    host = document.createElement('div');
    host.style.cssText = 'all:initial;position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;';
    const root = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = CSS;
    menuEl = h('div', { class: 'menu' });
    menuEl.hidden = true;
    barEl = h('div', { class: 'bar' });
    barEl.hidden = true;
    toastEl = h('div', { class: 'toast' });
    toastEl.hidden = true;

    const dl = h('button', { title: 'Download image (original format)' });
    dl.appendChild(icon('M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 20h14'));
    const more = h('button', { title: 'Choose format' });
    more.appendChild(icon('M6 9l6 6 6-6'));
    barEl.append(dl, more);

    dl.addEventListener('click', () => {
      if (hoverImg) download(hoverImg.currentSrc || hoverImg.src, hoverImg, 'original');
    });
    more.addEventListener('click', () => {
      if (!hoverImg) return;
      const r = more.getBoundingClientRect();
      openMenu(r.left, r.bottom + 4, hoverImg, true);
    });
    ['click', 'mousedown', 'mouseup', 'pointerdown', 'pointerup', 'contextmenu'].forEach((ev) =>
      barEl.addEventListener(ev, (e) => { e.stopPropagation(); if (ev === 'contextmenu') e.preventDefault(); })
    );
    barEl.addEventListener('mouseenter', () => clearTimeout(hideT));
    barEl.addEventListener('mouseleave', scheduleHide);

    root.append(style, menuEl, barEl, toastEl);
    document.documentElement.appendChild(host);
  }

  function toast(msg, ms = 2200, err = false) {
    ensureUI();
    toastEl.textContent = msg;
    toastEl.className = 'toast' + (err ? ' err' : '');
    toastEl.hidden = false;
    clearTimeout(toastT);
    if (ms) toastT = setTimeout(() => { toastEl.hidden = true; }, ms);
  }

  // ---------------------------------------------------------------- context menu
  function formatItem(f, src, imgEl) {
    const it = h('div', { class: 'item' }, h('span', { class: 'lbl', text: f.label }));
    it.addEventListener('click', () => {
      closeMenu();
      download(src, imgEl, f.id);
    });
    return it;
  }

  function buildMenu(imgEl, formatsOnly) {
    const src = imgEl.currentSrc || imgEl.src;
    menuEl.textContent = '';
    if (formatsOnly) {
      FORMATS.forEach((f) => menuEl.appendChild(formatItem(f, src, imgEl)));
      return;
    }
    const sub = h('div', { class: 'sub' });
    FORMATS.forEach((f) => sub.appendChild(formatItem(f, src, imgEl)));
    const parent = h('div', { class: 'has-sub' },
      h('div', { class: 'item' }, h('span', { class: 'lbl', text: 'Download image' }), h('span', { class: 'r', text: '\u25B8' })),
      sub);
    menuEl.appendChild(parent);

    menuEl.appendChild(h('div', { class: 'sep' }));
    const on = isHoverOn();
    const tog = h('div', { class: 'item' },
      h('span', { class: 'lbl', text: 'Hover download buttons' }),
      h('span', { class: 'sw' + (on ? ' on' : '') }));
    tog.addEventListener('click', () => {
      closeMenu();
      setHover(!isHoverOn());
    });
    menuEl.appendChild(tog);
    menuEl.appendChild(h('div', { class: 'hint', text: 'Shift + right-click for browser menu' }));
  }

  function openMenu(x, y, imgEl, formatsOnly) {
    ensureUI();
    buildMenu(imgEl, formatsOnly);
    menuEl.classList.remove('flip', 'up');
    menuEl.hidden = false;
    menuEl.style.left = '0px';
    menuEl.style.top = '0px';
    const w = menuEl.offsetWidth, hh = menuEl.offsetHeight;
    let l = x + 2, t = y + 2;
    if (l + w > innerWidth - 4) l = Math.max(4, x - w - 2);
    if (t + hh > innerHeight - 4) t = Math.max(4, y - hh - 2);
    menuEl.style.left = l + 'px';
    menuEl.style.top = t + 'px';
    if (!formatsOnly) {
      if (l + w + 180 > innerWidth && l > 180) menuEl.classList.add('flip');
      if (t + 210 > innerHeight && t > 210) menuEl.classList.add('up');
    }
    menuOpen = true;
  }

  function closeMenu() {
    if (menuEl) menuEl.hidden = true;
    menuOpen = false;
  }

  // ---------------------------------------------------------------- hover buttons
  function setHover(v) {
    GM_setValue(STORE_KEY, !!v);
    if (!v) hideBar();
    toast('Hover download buttons ' + (v ? 'ON' : 'OFF'));
  }

  function hideBar() {
    clearTimeout(hideT);
    if (barEl) barEl.hidden = true;
    hoverImg = null;
  }

  function scheduleHide() {
    clearTimeout(hideT);
    hideT = setTimeout(() => { if (!menuOpen) hideBar(); }, 180);
  }

  function positionBar() {
    if (!hoverImg || !hoverImg.isConnected) return hideBar();
    const r = hoverImg.getBoundingClientRect();
    if (r.width < MIN_HOVER_SIZE || r.height < MIN_HOVER_SIZE ||
        r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) {
      barEl.hidden = true;
      return;
    }
    barEl.hidden = false;
    const w = barEl.offsetWidth;
    const right = Math.min(r.right, innerWidth);
    barEl.style.left = Math.max(r.left, right - w - 6) + 'px';
    barEl.style.top = Math.max(r.top, 0) + 6 + 'px';
  }

  function showBar(img) {
    ensureUI();
    clearTimeout(hideT);
    hoverImg = img;
    positionBar();
  }

  // ---------------------------------------------------------------- events
  function pathHasHost(e) {
    return host && e.composedPath && e.composedPath().indexOf(host) !== -1;
  }

  function imageFromEvent(e) {
    const path = e.composedPath ? e.composedPath() : [e.target];
    for (const n of path) {
      if (n && n.tagName === 'IMG') return n;
    }
    return null;
  }

  window.addEventListener('contextmenu', (e) => {
    if (pathHasHost(e)) { e.preventDefault(); return; }
    if (e.shiftKey || e.ctrlKey) { closeMenu(); return; } // let the native menu through
    const img = imageFromEvent(e);
    if (!img) { closeMenu(); return; }
    e.preventDefault();
    e.stopImmediatePropagation();
    openMenu(e.clientX, e.clientY, img, false);
  }, true);

  window.addEventListener('mousedown', (e) => {
    if (menuOpen && !pathHasHost(e)) closeMenu();
  }, true);

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && menuOpen) closeMenu();
  }, true);

  window.addEventListener('blur', closeMenu);
  window.addEventListener('resize', () => { closeMenu(); if (hoverImg) positionBar(); });
  window.addEventListener('scroll', () => {
    if (menuOpen) closeMenu();
    if (hoverImg) positionBar();
  }, true);

  document.addEventListener('mouseover', (e) => {
    const t = e.target;
    if (!t || t.tagName !== 'IMG') return;
    if (!isHoverOn()) return;
    if (t === hoverImg) { clearTimeout(hideT); return; }
    const r = t.getBoundingClientRect();
    if (r.width < MIN_HOVER_SIZE || r.height < MIN_HOVER_SIZE) return;
    showBar(t);
  }, true);

  document.addEventListener('mouseout', (e) => {
    if (e.target === hoverImg) scheduleHide();
  }, true);

  GM_registerMenuCommand('Toggle hover download buttons', () => setHover(!isHoverOn()));
})();
