/* Lego Buyer front end. Plain JS, no build step. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  const state = {
    setNum: document.body.dataset.set || null,
    set: null,
    parts: [],
    figs: [],
    rev: -1,
    mode: localStorage.getItem('lb_mode') || 'have',
    search: '',
    sort: localStorage.getItem('lb_sort') || 'type',
    hideDone: localStorage.getItem('lb_hide_done') !== '0',
    who: localStorage.getItem('lb_who') || '',
    rows: new Map(), // key -> row element
    pollTimer: null,
  };

  // ---------- helpers ----------
  const api = async (url, opts = {}) => {
    const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
    if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); return null; }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
    return data;
  };
  // ---------- offline queue ----------
  // Progress writes that fail for network reasons are queued and replayed in order once the server
  // is reachable again. The optimistic UI state already reflects them.
  const qKey = () => `lb_queue:${state.setNum}`;
  const loadQueue = () => { try { return JSON.parse(localStorage.getItem(qKey()) || '[]'); } catch { return []; } };
  const saveQueue = (q) => { try { localStorage.setItem(qKey(), JSON.stringify(q)); } catch {} updateNetBanner(); };
  const isNetworkError = (e) => e instanceof TypeError || /Failed to fetch|NetworkError|Load failed/i.test(String(e && e.message));
  let flushing = false;
  const flushQueue = async () => {
    if (flushing || !state.setNum) return;
    let q = loadQueue(); if (!q.length) return;
    flushing = true;
    try {
      while (q.length) {
        const item = q[0];
        try {
          const res = await api(`/api/sets/${state.setNum}/progress`, { method: 'POST', body: JSON.stringify(item) });
          if (!res) return;
          state.rev = res.rev;
        } catch (e) {
          if (isNetworkError(e)) return; // still offline; try again later
          // Server rejected it (bad key etc.): drop it rather than block the queue.
        }
        q.shift(); saveQueue(q);
      }
      toast('Synced pending taps');
      state.rev = -1; await poll();
    } finally { flushing = false; updateNetBanner(); }
  };
  const updateNetBanner = () => {
    const b = $('#netbar'); if (!b) return;
    const n = loadQueue().length;
    const offline = !navigator.onLine || state.serverDown;
    if (!offline && !n) { b.hidden = true; return; }
    b.hidden = false;
    b.textContent = offline ? `Offline — ${n} tap${n === 1 ? '' : 's'} saved on this phone, will sync when back online` : `Syncing ${n} pending tap${n === 1 ? '' : 's'}…`;
  };

  const toast = (msg, ms = 2200) => {
    const t = $('#toast'); t.textContent = msg; t.hidden = false;
    clearTimeout(t._t); t._t = setTimeout(() => (t.hidden = true), ms);
  };
  const countAlt = () => !!(state.set && state.set.count_alt);
  const needed = (it) => Math.max(0, it.quantity - it.owned);
  const remaining = (it) => Math.max(0, it.quantity - it.owned - it.found_exact - (countAlt() ? it.found_alt : 0));
  const ago = (ts) => {
    if (!ts) return '';
    const s = Math.max(0, (Date.now() / 1000) - ts);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  };
  const ensureWho = () => {
    if (state.who) return state.who;
    const n = (prompt('Your name (shown next to parts you tap):') || '').trim().slice(0, 40);
    if (n) { state.who = n; localStorage.setItem('lb_who', n); $('#btn-who').textContent = n; }
    return state.who;
  };

  // ---------- items ----------
  const visibleItems = () => {
    const items = [];
    for (const p of state.parts) {
      if (p.is_spare && !state.set.include_spares) continue;
      items.push(p);
    }
    if (state.set.include_minifigs) for (const f of state.figs) items.push(f);
    return items;
  };
  const isDone = (it) => {
    if (state.mode === 'have') return it.owned >= it.quantity;
    return remaining(it) === 0;
  };
  const matches = (it, q) => {
    if (!q) return true;
    const hay = [it.part_name || it.name, it.color_name, it.element_id, it.part_num, it.fig_num, it.cat_name]
      .filter(Boolean).join(' ').toLowerCase();
    return q.split(/\s+/).every((w) => hay.includes(w));
  };

  // Group rows into cards: parts by design (part_num), minifigs one per card.
  const buildCards = () => {
    const q = state.search.trim().toLowerCase();
    const cards = new Map();
    for (const it of visibleItems()) {
      if (!matches(it, q)) continue;
      if (state.hideDone && isDone(it)) continue;
      if (state.mode === 'trip' && needed(it) === 0 && state.hideDone) continue;
      if (state.mode === 'buy' && remaining(it) === 0) continue;
      const id = it.kind === 'minifig' ? `fig:${it.fig_num}` : `part:${it.part_num}`;
      if (!cards.has(id)) {
        cards.set(id, {
          id,
          design: it.kind === 'minifig' ? it.fig_num : it.part_num,
          title: it.kind === 'minifig' ? it.name : it.part_name,
          cat: it.kind === 'minifig' ? 'Minifigure' : (it.cat_name || ''),
          rows: [],
        });
      }
      cards.get(id).rows.push(it);
    }
    const list = [...cards.values()];
    for (const c of list) c.rows.sort((a, b) => (a.color_name || '').localeCompare(b.color_name || ''));
    const cmp = {
      type: (a, b) => a.cat.localeCompare(b.cat) || a.title.localeCompare(b.title),
      color: (a, b) => (a.rows[0].color_name || '').localeCompare(b.rows[0].color_name || '') || a.title.localeCompare(b.title),
      name: (a, b) => a.title.localeCompare(b.title),
      remaining: (a, b) => sum(b.rows, remaining) - sum(a.rows, remaining) || a.title.localeCompare(b.title),
      recent: (a, b) => maxUpd(b.rows) - maxUpd(a.rows) || a.title.localeCompare(b.title),
    }[state.sort] || ((a, b) => 0);
    list.sort(cmp);
    return list;
  };
  const sum = (rows, f) => rows.reduce((n, r) => n + f(r), 0);
  const maxUpd = (rows) => Math.max(0, ...rows.map((r) => r.updated_at || 0));

  // ---------- rendering ----------
  const tplCard = $('#tpl-card').content;
  const tplRow = $('#tpl-row').content;

  const render = () => {
    const grid = $('#grid');
    const cards = buildCards();
    const scroll = window.scrollY;
    grid.replaceChildren();
    state.rows.clear();
    for (const c of cards) {
      const el = tplCard.cloneNode(true).firstElementChild;
      $('.design', el).textContent = c.cat ? `${c.cat.toUpperCase()} · DESIGN ${c.design}` : `DESIGN ${c.design}`;
      $('.part-name', el).textContent = c.title;
      const rows = $('.rows', el);
      for (const it of c.rows) {
        const r = tplRow.cloneNode(true).firstElementChild;
        r.dataset.key = it.key;
        const im = $('img', r);
        im.alt = '';
        im.addEventListener('error', () => im.closest('.pic').classList.add('noimg'), { once: true });
        if (it.img_url) im.src = it.img_url; else im.closest('.pic').classList.add('noimg');
        const sw = $('.swatch', r);
        if (it.color_rgb) sw.style.background = `#${it.color_rgb}`; else sw.hidden = true;
        $('.color-name', r).textContent = it.kind === 'minifig' ? 'Complete minifigure' : it.color_name;
        $('.element', r).textContent = it.kind === 'minifig' ? it.fig_num : (it.element_id ? `Element ${it.element_id}` : `Design ${it.part_num}`);
        if (it.is_spare) $('.meta', r).insertAdjacentHTML('beforeend', '<span class="tag">spare</span>');
        if (it.lego_price != null) {
          const pr = $('.price', r); pr.hidden = false;
          pr.innerHTML = `<b>LEGO:</b> $${it.lego_price.toFixed(2)} each · $${(it.lego_price * remaining(it)).toFixed(2)} needed${it.lego_tier ? ' · ' + it.lego_tier : ''}`;
        }
        rows.appendChild(r);
        state.rows.set(it.key, r);
        paintRow(it, r);
      }
      grid.appendChild(el);
    }
    $('#empty').hidden = cards.length > 0;
    $('#empty').textContent = state.search ? 'Nothing matches that search.' :
      state.mode === 'have' ? 'Everything is marked as owned. Switch to Trip.' :
      state.mode === 'trip' ? 'Nothing left to find. Switch to Buy.' : 'Nothing left to buy.';
    $('#buy-panel').hidden = state.mode !== 'buy';
    renderSummary();
    window.scrollTo(0, scroll);
  };

  const paintRow = (it, r) => {
    const need = needed(it), rem = remaining(it);
    const qty = $('.qty', r);
    let have, total, label;
    if (state.mode === 'have') { have = it.owned; total = it.quantity; label = 'owned'; qty.textContent = `×${it.quantity}`; }
    else { have = need - rem; total = need; label = 'found'; qty.textContent = `×${rem}`; }
    qty.classList.toggle('done', total > 0 && have >= total);
    $('.bar-fill', r).style.width = total ? `${Math.min(100, (have / total) * 100)}%` : '0%';
    $('.count-text', r).innerHTML = `<b>${have}</b> / ${total} ${label}`;
    $('.last', r).textContent = it.updated_by ? `${it.updated_by} · ${ago(it.updated_at)}` : '';
    r.classList.toggle('complete', total > 0 && have >= total);

    const actions = $('.actions', r); actions.replaceChildren();
    const mistake = $('.mistake', r); const undo = $('.undo', r); undo.replaceChildren();
    if (state.mode === 'have') {
      mistake.hidden = true;
      actions.append(
        btn('−', 'minus', () => tap(it, 'owned', -1), it.owned <= 0),
        btn(`Have <small>${it.owned} of ${it.quantity}</small>`, 'have', () => tap(it, 'owned', 1), it.owned >= it.quantity),
        btn('All', 'all', () => setVal(it, 'owned', it.quantity), it.owned >= it.quantity),
      );
    } else if (state.mode === 'trip') {
      mistake.hidden = false;
      actions.append(
        btn(`<span class="chk">✓</span><span><b>Exact</b><small>${it.found_exact} found</small></span>`, 'exact', () => tap(it, 'found_exact', 1), rem <= 0),
        btn(`<span class="chk alt">≈</span><span><b>Alt</b><small>${it.found_alt} found</small></span>`, 'alt', () => tap(it, 'found_alt', 1), rem <= 0 && countAlt()),
      );
      if (it.found_exact > 0) undo.append(btn('− exact', 'link', () => tap(it, 'found_exact', -1)));
      if (it.found_alt > 0) undo.append(btn('− alt', 'link', () => tap(it, 'found_alt', -1)));
      if (!undo.children.length) undo.append(Object.assign(document.createElement('span'), { className: 'muted', textContent: 'nothing to undo' }));
    } else {
      mistake.hidden = true;
      const parts = [];
      if (it.owned) parts.push(`${it.owned} owned`);
      if (it.found_exact) parts.push(`${it.found_exact} found`);
      if (it.found_alt) parts.push(`${it.found_alt} alt`);
      actions.innerHTML = `<div class="order">Order <b>${rem}</b>${parts.length ? `<small>${parts.join(' · ')}</small>` : ''}</div>`;
    }
  };

  const btn = (html, cls, onClick, disabled = false) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = `act ${cls}`; b.innerHTML = html; b.disabled = disabled;
    b.addEventListener('click', (e) => { e.preventDefault(); onClick(); });
    return b;
  };

  const renderSummary = () => {
    const items = visibleItems();
    const lots = items.length;
    const pieces = sum(items, (i) => i.quantity);
    const owned = sum(items, (i) => Math.min(i.owned, i.quantity));
    const found = sum(items, (i) => Math.min(i.found_exact + (countAlt() ? i.found_alt : 0), needed(i)));
    const rem = sum(items, remaining);
    $('#summary').innerHTML = `
      <span><b>${pieces}</b> pieces in <b>${lots}</b> lots</span>
      <span class="s-own"><b>${owned}</b> owned</span>
      <span class="s-found"><b>${found}</b> found at store</span>
      <span class="s-rem"><b>${rem}</b> still to buy</span>`;
  };

  const findItem = (key) => state.parts.find((p) => p.key === key) || state.figs.find((f) => f.key === key);

  // ---------- updates ----------
  const applyProgress = (it, row) => { Object.assign(it, row); const el = state.rows.get(it.key); if (el) paintRow(it, el); renderSummary(); };

  const tap = async (it, field, delta) => {
    const who = ensureWho();
    const before = { owned: it.owned, found_exact: it.found_exact, found_alt: it.found_alt };
    // Optimistic update.
    it[field] = Math.max(0, it[field] + delta); it.updated_by = who || it.updated_by; it.updated_at = Date.now() / 1000;
    applyProgress(it, {});
    try {
      const res = await api(`/api/sets/${state.setNum}/progress`, { method: 'POST', body: JSON.stringify({ key: it.key, field, delta, who }) });
      if (!res) return;
      state.rev = res.rev; delete res.rev;
      applyProgress(it, res);
      if (state.hideDone && isDone(it)) scheduleRender();
    } catch (e) {
      if (isNetworkError(e)) { const q = loadQueue(); q.push({ key: it.key, field, delta, who }); saveQueue(q); state.serverDown = true; updateNetBanner(); return; }
      Object.assign(it, before); applyProgress(it, {}); toast(e.message);
    }
  };
  const setVal = async (it, field, value) => {
    const who = ensureWho();
    it[field] = value; applyProgress(it, {});
    try {
      const res = await api(`/api/sets/${state.setNum}/progress`, { method: 'POST', body: JSON.stringify({ key: it.key, field, value, who }) });
      if (!res) return;
      state.rev = res.rev; delete res.rev; applyProgress(it, res);
      if (state.hideDone && isDone(it)) scheduleRender();
    } catch (e) {
      if (isNetworkError(e)) { const q = loadQueue(); q.push({ key: it.key, field, value, who }); saveQueue(q); state.serverDown = true; updateNetBanner(); return; }
      toast(e.message);
    }
  };
  let renderT = null;
  const scheduleRender = () => { clearTimeout(renderT); renderT = setTimeout(render, 700); };

  const poll = async () => {
    if (!state.setNum || document.hidden) return;
    if (loadQueue().length) { await flushQueue(); if (loadQueue().length) return; }
    try {
      const res = await api(`/api/sets/${state.setNum}/progress?rev=${state.rev}`);
      if (state.serverDown) { state.serverDown = false; updateNetBanner(); }
      if (!res || !res.changed) return;
      state.rev = res.rev;
      if (res.set.loaded_at !== state.set.loaded_at) { await loadSet(); toast('Set was refreshed'); return; }
      const settingsChanged = ['include_spares', 'include_minifigs', 'count_alt'].some((k) => state.set[k] !== res.set[k]);
      state.set = res.set;
      let structural = settingsChanged;
      for (const it of [...state.parts, ...state.figs]) {
        const p = res.progress[it.key] || { owned: 0, found_exact: 0, found_alt: 0, updated_at: null, updated_by: null };
        const changed = ['owned', 'found_exact', 'found_alt'].some((k) => it[k] !== p[k]);
        if (changed) { Object.assign(it, p); const el = state.rows.get(it.key); if (el) paintRow(it, el); if (state.hideDone && isDone(it)) structural = true; if (!el) structural = true; }
      }
      if (structural) { syncFilterUI(); render(); } else renderSummary();
    } catch (e) { if (isNetworkError(e)) { state.serverDown = true; updateNetBanner(); } }
  };

  // ---------- set loading ----------
  const loadSet = async () => {
    const data = await api(`/api/sets/${state.setNum}`);
    if (!data) return;
    state.set = data.set; state.rev = data.rev;
    state.parts = data.parts.map((p) => ({ ...p, kind: 'part' }));
    state.figs = data.minifigs.map((f) => ({ ...f, kind: 'minifig' }));
    for (const q of loadQueue()) { // pending offline taps still count
      const it = findItem(q.key); if (!it) continue;
      if ('value' in q) it[q.field] = Math.max(0, q.value); else it[q.field] = Math.max(0, it[q.field] + q.delta);
      it.updated_by = q.who || it.updated_by;
    }
    updateNetBanner();
    $('#hdr-small').textContent = `LEGO ${state.set.set_num.replace(/-1$/, '')}`;
    $('#hdr-title').textContent = state.set.name;
    document.title = `${state.set.set_num.replace(/-1$/, '')} · Lego Buyer`;
    $('#link-pdf').href = `/s/${state.setNum}/checklist.pdf?mode=${state.mode === 'have' ? 'have' : 'trip'}`;
    $('#link-print').href = `/s/${state.setNum}/checklist?mode=${state.mode === 'have' ? 'have' : 'trip'}`;
    syncFilterUI();
    render();
  };

  const syncFilterUI = () => {
    $('#f-spares').checked = !!state.set.include_spares;
    $('#f-minifigs').checked = !!state.set.include_minifigs;
    $('#f-count-alt').checked = !!state.set.count_alt;
    $('#f-hide-done').checked = state.hideDone;
    const n = [state.set.include_spares, state.set.include_minifigs, !state.set.count_alt, !state.hideDone].filter(Boolean).length;
    $('#filter-count').hidden = n === 0; $('#filter-count').textContent = n;
    $('#link-pdf').href = `/s/${state.setNum}/checklist.pdf?mode=${state.mode === 'have' ? 'have' : 'trip'}`;
    $('#link-print').href = `/s/${state.setNum}/checklist?mode=${state.mode === 'have' ? 'have' : 'trip'}`;
  };

  const saveSetting = async (patch) => {
    const res = await api(`/api/sets/${state.setNum}/settings`, { method: 'POST', body: JSON.stringify(patch) });
    if (!res) return;
    state.set = res.set; state.rev = res.rev; syncFilterUI(); render();
  };

  // ---------- exports ----------
  const buyList = () => visibleItems().filter((i) => remaining(i) > 0 && i.kind === 'part');
  const blXml = () => {
    const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const items = buyList().map((i) => {
      const bl = (i.external_ids && i.external_ids.part && i.external_ids.part.BrickLink || [])[0] || i.part_num;
      const blc = (i.external_ids && i.external_ids.color && i.external_ids.color.BrickLink && i.external_ids.color.BrickLink.ext_ids || [])[0];
      return `<ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>${esc(bl)}</ITEMID>${blc != null ? `<COLOR>${blc}</COLOR>` : ''}<MINQTY>${remaining(i)}</MINQTY><CONDITION>N</CONDITION></ITEM>`;
    });
    return `<INVENTORY>\n${items.join('\n')}\n</INVENTORY>`;
  };
  const csv = () => ['part_num,color,color_id,element_id,qty_to_buy,name']
    .concat(buyList().map((i) => [i.part_num, `"${i.color_name}"`, i.color_id, i.element_id || '', remaining(i), `"${(i.part_name || '').replace(/"/g, '""')}"`].join(',')))
    .join('\n');
  const copy = async (text, what) => {
    try { await navigator.clipboard.writeText(text); toast(`${what} copied`); }
    catch { prompt(`Copy this ${what}:`, text); }
  };

  // ---------- wiring ----------
  const setMode = (m) => {
    state.mode = m; localStorage.setItem('lb_mode', m);
    $$('.stage').forEach((b) => b.classList.toggle('active', b.dataset.mode === m));
    if (state.set) { syncFilterUI(); render(); }
  };

  const init = () => {
    $('#btn-who').textContent = state.who || '?';
    $('#btn-who').addEventListener('click', () => { state.who = ''; localStorage.removeItem('lb_who'); ensureWho(); $('#btn-who').textContent = state.who || '?'; });
    $('#btn-switch').addEventListener('click', () => (location.href = '/'));

    // Home
    $('#load-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const v = $('#set-input').value.trim(); if (!v) return;
      $('#btn-load').disabled = true; $('#load-status').textContent = 'Loading from Rebrickable… this takes a few seconds.';
      try {
        const res = await api('/api/sets', { method: 'POST', body: JSON.stringify({ set_num: v }) });
        if (res) location.href = `/s/${res.set.set_num}`;
      } catch (err) { $('#load-status').textContent = err.message; $('#btn-load').disabled = false; }
    });
    $$('[data-del]').forEach((b) => b.addEventListener('click', async () => {
      if (!confirm(`Remove ${b.dataset.del} and all its counts?`)) return;
      await api(`/api/sets/${b.dataset.del}`, { method: 'DELETE' }); location.reload();
    }));

    if (!state.setNum) { $('#home').hidden = false; $('#btn-switch').hidden = true; $('#btn-reload').hidden = true; return; }
    $('#workspace').hidden = false;

    $('#btn-reload').addEventListener('click', async () => {
      if (!confirm('Re-fetch this set from Rebrickable? Your counts are kept.')) return;
      toast('Reloading…', 6000);
      try { await api('/api/sets', { method: 'POST', body: JSON.stringify({ set_num: state.setNum }) }); await loadSet(); toast('Set refreshed'); }
      catch (e) { toast(e.message); }
    });
    $$('.stage').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
    $('#search').addEventListener('input', (e) => { state.search = e.target.value; render(); });
    $('#sort').value = state.sort;
    $('#sort').addEventListener('change', (e) => { state.sort = e.target.value; localStorage.setItem('lb_sort', state.sort); render(); });
    $('#btn-filters').addEventListener('click', () => { $('#filters').hidden = !$('#filters').hidden; });
    $('#f-hide-done').addEventListener('change', (e) => { state.hideDone = e.target.checked; localStorage.setItem('lb_hide_done', state.hideDone ? '1' : '0'); syncFilterUI(); render(); });
    $('#f-spares').addEventListener('change', (e) => saveSetting({ include_spares: e.target.checked }));
    $('#f-minifigs').addEventListener('change', (e) => saveSetting({ include_minifigs: e.target.checked }));
    $('#f-count-alt').addEventListener('change', (e) => saveSetting({ count_alt: e.target.checked }));
    $('#btn-reset-trip').addEventListener('click', async () => {
      if (!confirm('Reset all Exact/Alt store counts for this set? Owned counts are kept.')) return;
      await api(`/api/sets/${state.setNum}/reset`, { method: 'POST', body: JSON.stringify({ fields: ['found_exact', 'found_alt'] }) });
      state.rev = -1; await loadSet(); toast('Trip counts reset');
    });
    $('#btn-copy-bl').addEventListener('click', () => copy(blXml(), 'BrickLink XML'));
    $('#btn-copy-csv').addEventListener('click', () => copy(csv(), 'CSV'));

    // Clicking a row's picture also acts as the primary tap (handy with one thumb at the wall).
    $('#grid').addEventListener('click', (e) => {
      const pic = e.target.closest('.pic'); if (!pic) return;
      const key = pic.closest('.row').dataset.key; const it = findItem(key); if (!it) return;
      if (state.mode === 'have' && it.owned < it.quantity) tap(it, 'owned', 1);
      else if (state.mode === 'trip' && remaining(it) > 0) tap(it, 'found_exact', 1);
    });
    $('#grid').addEventListener('click', (e) => {
      const t = e.target.closest('.mistake-toggle'); if (!t) return;
      t.closest('.mistake').classList.toggle('open');
    });

    setMode(state.mode);
    loadSet().catch((e) => toast(e.message));
    state.pollTimer = setInterval(poll, 2500);
    window.addEventListener('online', () => { updateNetBanner(); flushQueue(); });
    window.addEventListener('offline', updateNetBanner);
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  };

  init();
})();
