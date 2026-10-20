// ==UserScript==
// @name         Claude Session Usage (estimate)
// @namespace    local.claude-usage-est
// @version      2.0.0
// @description  Estimates your 5-hour session usage and reset time. OLED pill, details on hover.
// @match        https://claude.ai/*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(() => {
  'use strict';

  /* ================= config (tweak freely) ================= */
  const WINDOW_MS = 5 * 60 * 60 * 1000; // session window, starts at your first message
  const CHARS_PER_TOKEN = 3.5;          // rough text -> token ratio
  const CACHE_READ_WEIGHT = 0.1;        // re-reading existing chat history is cheap
  const OUTPUT_WEIGHT = 5;              // generated tokens count for more than input
  const DEFAULT_CAP = 300000;           // placeholder cap (weighted units) until you calibrate
  const KEY = 'oledUsageEst.v1';
  const DEBUG = true;                   // logs "[usage-est]" lines to the console

  const log = (...a) => DEBUG && console.log('[usage-est]', ...a);
  const tok = chars => Math.ceil(chars / CHARS_PER_TOKEN);

  /* ================= storage ================= */
  const fresh = () => ({ windowStart: 0, units: 0, msgs: 0, cap: DEFAULT_CAP, calibrated: false });
  const load = () => {
    try { return Object.assign(fresh(), JSON.parse(localStorage.getItem(KEY)) || {}); }
    catch { return fresh(); }
  };
  const save = s => { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch {} };

  function bump(units, newMsg) {
    const s = load();
    const now = Date.now();
    if (!s.windowStart || now >= s.windowStart + WINDOW_MS) {
      s.windowStart = now; s.units = 0; s.msgs = 0;
    }
    s.units += units;
    if (newMsg) s.msgs += 1;
    save(s);
    render();
  }

  function view() {
    const s = load();
    const active = !!s.windowStart && Date.now() < s.windowStart + WINDOW_MS;
    const used = active ? s.units : 0;
    const pct = Math.min(100, Math.round((used / s.cap) * 100));
    return { s, active, used, pct, resetAt: active ? s.windowStart + WINDOW_MS : 0 };
  }

  /* ================= tracking ================= */
  const convCtx = new Map(); // conversationId -> estimated context tokens

  const domTokens = () => {
    const el = document.querySelector('main');
    return el ? tok(el.innerText.length) : 0;
  };

  function startTurn(url, bodyText) {
    const conv = (url.match(/chat_conversations\/([^/?]+)/) || [])[1] || 'unknown';
    let chars = 0;
    try {
      const b = JSON.parse(bodyText || '{}');
      if (typeof b.prompt === 'string') chars += b.prompt.length;
      for (const a of b.attachments || []) chars += (a.extracted_content || '').length;
    } catch {}
    const P = tok(chars);

    let hist = convCtx.get(conv);
    if (hist === undefined) hist = Math.max(0, domTokens() - P); // chat we haven't seen yet
    convCtx.set(conv, hist + P);

    bump(hist * CACHE_READ_WEIGHT + P, true);
    log('turn started', { conv, newTokens: P, historyTokens: hist });
    return { conv };
  }

  async function watchStream(res, turn) {
    if (!res.body) return;
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '', chars = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 1);
          if (!line.startsWith('data:')) continue;
          try {
            const j = JSON.parse(line.slice(5));
            const d = j.delta || {};
            chars += (d.text || d.thinking || '').length;
            if (typeof j.completion === 'string') chars += j.completion.length;
          } catch {}
        }
      }
    } catch {} // aborted stream: still count what arrived
    const O = tok(chars);
    convCtx.set(turn.conv, (convCtx.get(turn.conv) || 0) + O);
    bump(O * OUTPUT_WEIGHT, false);
    log('turn finished', { outputTokens: O });
  }

  const seen = new Set();
  const debugSeen = url => {
    const k = url.replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/gi, ':id').split('?')[0];
    if (!seen.has(k)) { seen.add(k); log('POST seen:', k); }
  };

  const origFetch = window.fetch;
  window.fetch = async function (input, init) {
    let url = '', method = 'GET', bodyText = null;
    try {
      url = typeof input === 'string' ? input : (input && input.url) || String(input);
      method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      bodyText = init && typeof init.body === 'string' ? init.body : null;
    } catch {}

    const tracked = method === 'POST' &&
      /\/chat_conversations\/[^/]+\/(?:retry_)?completion/.test(url);
    if (method === 'POST' && /\/api\//.test(url)) debugSeen(url);

    let turn = null;
    if (tracked) {
      try {
        if (!bodyText && typeof Request !== 'undefined' && input instanceof Request) {
          bodyText = await input.clone().text().catch(() => null);
        }
        turn = startTurn(url, bodyText);
      } catch (e) { log('startTurn failed', e); }
    }

    const res = await origFetch.apply(this, arguments);
    if (tracked && turn) {
      try { watchStream(res.clone(), turn); } catch (e) { log('watchStream failed', e); }
    }
    return res;
  };

  /* ================= UI ================= */
  const CSS = `
    #ue-wrap{position:fixed;right:14px;bottom:14px;z-index:2147483000;font-family:inherit;font-size:12px;line-height:1.35;color:#fff}
    #ue-pill{display:flex;align-items:center;gap:8px;padding:6px 11px;background:#000;border:1px solid rgba(255,255,255,.16);border-radius:999px;user-select:none;transition:border-color .2s}
    #ue-wrap.ue-hot #ue-pill{border-color:#fff}
    .ue-track{display:block;width:34px;height:3px;background:#222;border-radius:2px;overflow:hidden}
    .ue-fill{display:block;height:100%;width:0;background:#fff;transition:width .4s}
    #ue-tip{position:absolute;right:0;bottom:100%;padding-bottom:8px;opacity:0;visibility:hidden;transition:opacity .15s,visibility .15s}
    #ue-wrap:hover #ue-tip{opacity:1;visibility:visible}
    #ue-card{width:252px;padding:12px 14px;background:#0d0d0d;border:1px solid rgba(255,255,255,.16);border-radius:12px}
    .ue-title{font-weight:600;margin-bottom:8px}
    .ue-bar{height:6px;background:#1f1f1f;border-radius:3px;overflow:hidden;margin:2px 0 8px}
    .ue-bar>span{display:block;height:100%;background:#fff}
    .ue-big{font-size:18px;font-weight:600;margin:6px 0 2px}
    .ue-line{color:#e5e5e5;margin-top:2px}
    .ue-dim{color:#8a8a8a;margin-top:4px}
    .ue-btns{display:flex;gap:6px;margin-top:10px}
    .ue-btns button{flex:1;background:#000;color:#fff;border:1px solid rgba(255,255,255,.22);border-radius:8px;padding:6px 0;font:inherit;cursor:pointer}
    .ue-btns button:hover{background:#fff;color:#000}
  `;

  let wrap, pillText, pillFill, info;

  const el = (cls, text) => {
    const d = document.createElement('div');
    d.className = cls;
    if (text != null) d.textContent = text;
    return d;
  };
  const fmtDur = ms => {
    const m = Math.max(0, Math.ceil(ms / 60000));
    const h = Math.floor(m / 60);
    return h ? `${h}h ${m % 60}m` : `${m}m`;
  };
  const fmtTime = t => new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

  function calibrate() {
    const v = view();
    if (!v.active || !v.s.units) {
      alert('Nothing tracked in this session yet. Send a few messages, then calibrate.');
      return;
    }
    const pct = parseFloat(prompt('Your real session usage right now, in % (Settings > Usage):'));
    if (!(pct > 0 && pct <= 100)) return;
    const s = load();
    s.cap = Math.max(1, s.units / (pct / 100));
    s.calibrated = true;
    save(s);
    render();
  }

  function resetWindow() {
    if (!confirm('Reset the tracked session? (keeps your calibration)')) return;
    const s = load();
    s.windowStart = 0; s.units = 0; s.msgs = 0;
    save(s);
    render();
  }

  function mount() {
    if (wrap) {
      if (!wrap.isConnected && document.body) document.body.appendChild(wrap);
      return;
    }
    if (!document.body) return;

    const style = document.createElement('style');
    style.textContent = CSS;
    document.documentElement.appendChild(style);

    wrap = document.createElement('div');
    wrap.id = 'ue-wrap';

    const tip = document.createElement('div');
    tip.id = 'ue-tip';
    const card = document.createElement('div');
    card.id = 'ue-card';
    info = document.createElement('div');
    const btns = el('ue-btns');
    for (const [label, fn] of [['Calibrate', calibrate], ['Reset window', resetWindow]]) {
      const b = document.createElement('button');
      b.textContent = label;
      b.addEventListener('click', fn);
      btns.appendChild(b);
    }
    card.append(info, btns);
    tip.appendChild(card);

    const pill = document.createElement('div');
    pill.id = 'ue-pill';
    pillText = document.createElement('span');
    const track = document.createElement('span');
    track.className = 'ue-track';
    pillFill = document.createElement('span');
    pillFill.className = 'ue-fill';
    track.appendChild(pillFill);
    pill.append(pillText, track);

    wrap.append(tip, pill);
    wrap.addEventListener('mouseenter', render);
    document.body.appendChild(wrap);
    render();
  }

  function render() {
    if (!wrap) return;
    const v = view();
    pillText.textContent = `~${v.pct}%`;
    pillFill.style.width = v.pct + '%';
    wrap.classList.toggle('ue-hot', v.pct >= 90);

    const bar = el('ue-bar');
    const fill = document.createElement('span');
    fill.style.width = v.pct + '%';
    bar.appendChild(fill);

    const rows = [
      el('ue-title', 'Session usage (estimate)'),
      bar,
      el('ue-big', `${v.pct}%`),
      v.active
        ? el('ue-line', `Resets in ${fmtDur(v.resetAt - Date.now())} · ${fmtTime(v.resetAt)}`)
        : el('ue-line', 'No active session. A new 5-hour window starts with your next message.'),
      v.active ? el('ue-dim', `Started ${fmtTime(v.s.windowStart)} · ${v.s.msgs} message${v.s.msgs === 1 ? '' : 's'} tracked`) : null,
      !v.s.calibrated ? el('ue-dim', 'Uncalibrated guess. Hit Calibrate with your real % from Settings > Usage.') : null,
      !v.s.msgs && !v.active ? el('ue-dim', 'Never moves? Check the console for [usage-est] lines.') : null,
    ].filter(Boolean);
    info.replaceChildren(...rows);
  }

  window.addEventListener('storage', e => { if (e.key === KEY) render(); });
  setInterval(() => { mount(); render(); }, 5000);
  if (document.body) mount();
  else document.addEventListener('DOMContentLoaded', mount);
})();
