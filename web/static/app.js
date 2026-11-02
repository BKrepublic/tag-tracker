(() => {
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  };

  const map = L.map('map', { preferCanvas: true, zoomControl: false }).setView([20, 0], 2);
  L.control.zoom({ position: 'topright' }).addTo(map);

  const gsub = { subdomains: ['mt0', 'mt1', 'mt2', 'mt3'], maxZoom: 21, attribution: '&copy; Google' };
  const bases = {
    'OpenStreetMap': { layer: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }), imagery: false },
    'Google Streets': { layer: L.tileLayer('https://{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', gsub), imagery: false },
    'Google Satellite': { layer: L.tileLayer('https://{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', gsub), imagery: true },
    'Google Hybrid': { layer: L.tileLayer('https://{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', gsub), imagery: true },
    'Satellite (Esri)': { layer: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Imagery &copy; Esri, Maxar' }), imagery: true },
  };
  const applyImagery = (name) => document.body.classList.toggle('imagery', !!bases[name]?.imagery);
  const saved = store.get('layer');
  const startName = bases[saved] ? saved : 'OpenStreetMap';
  bases[startName].layer.addTo(map);
  applyImagery(startName);
  const control = {};
  Object.entries(bases).forEach(([name, b]) => { control[name] = b.layer; });
  L.control.layers(control, null, { position: 'topright' }).addTo(map);
  map.on('baselayerchange', (e) => { applyImagery(e.name); store.set('layer', e.name); });

  // "Center on tag" control
  const Recenter = L.Control.extend({
    options: { position: 'topright' },
    onAdd() {
      const b = L.DomUtil.create('button', 'leaflet-bar recenter-btn');
      b.innerHTML = '◎'; b.title = 'Center on tag';
      L.DomEvent.on(b, 'click', (ev) => {
        L.DomEvent.stop(ev);
        const d = devices.find((x) => x.id === deviceId);
        if (d && d.last_lat != null) map.setView([d.last_lat, d.last_lon], 16);
      });
      return b;
    },
  });
  map.addControl(new Recenter());

  const layers = { track: L.layerGroup().addTo(map), points: L.layerGroup().addTo(map), live: L.layerGroup().addTo(map), play: L.layerGroup().addTo(map) };
  let devices = [], deviceId = store.get('device'), range = { h: store.get('range') || '168' };
  let points = [], fitted = false, playTimer = null;

  // Runs against either the web server (fetch) or, inside the Android app, the Native bridge.
  const NATIVE = window.Native;

  async function api(path, opts = {}) {
    const r = await fetch(path, { credentials: 'same-origin', ...opts });
    if (r.status === 401) { location.href = '/login'; throw new Error('logged out'); }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }

  const data = {
    devices: () => NATIVE ? JSON.parse(NATIVE.devices()) : api('/api/devices'),
    history: (id, s, e) => NATIVE ? JSON.parse(NATIVE.history(id, s, e))
      : api(`/api/history/${encodeURIComponent(id)}?start=${s}&end=${e}`),
    pollNow: () => NATIVE ? NATIVE.pollNow() : api('/api/poll-now', { method: 'POST' }),
    locate: (id) => NATIVE ? NATIVE.locateNow(id) : api(`/api/locate/${encodeURIComponent(id)}`, { method: 'POST' }),
    sound: (id) => NATIVE ? NATIVE.playSound(id) : api(`/api/sound/${encodeURIComponent(id)}`, { method: 'POST' }),
    setParked: (on) => NATIVE ? NATIVE.setParked(on)
      : api('/api/guard', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parked: on }) }),
    rename: (id, name) => NATIVE ? NATIVE.rename(id, name)
      : api(`/api/devices/${encodeURIComponent(id)}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }),
  };

  const fmt = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  function ago(ts) {
    const s = Date.now() / 1000 - ts;
    if (s < 90) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  }
  function km(a, b) {
    const R = 6371, r = Math.PI / 180;
    const dLat = (b.lat - a.lat) * r, dLon = (b.lon - a.lon) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // Reverse-geocode a point to a short address (OpenStreetMap Nominatim), cached per ~11m.
  const addrCache = {};
  async function addressFor(lat, lon) {
    // Never send raw location coordinates for reverse-geocoding without consent.
    if (store.get('allowAddressLookup') !== '1') return '';
    const k = lat.toFixed(4) + ',' + lon.toFixed(4);
    if (addrCache[k] !== undefined) return addrCache[k];
    try {
      const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=16&lat=${lat}&lon=${lon}`,
        { headers: { 'Accept': 'application/json' } });
      const j = await r.json();
      const a = j && j.display_name ? j.display_name.split(',').slice(0, 3).join(',').trim() : '';
      addrCache[k] = a; return a;
    } catch (e) { addrCache[k] = ''; return ''; }
  }

  function currentRange() {
    const now = Math.floor(Date.now() / 1000);
    if (range.h === 'today') { const d = new Date(); d.setHours(0, 0, 0, 0); return [Math.floor(d / 1000), now]; }
    if (range.h === 'custom') return [range.start, range.end];
    return [now - Number(range.h) * 3600, now];
  }

  // ---------- devices ----------
  async function loadDevices() {
    const res = await data.devices();
    devices = res.devices;
    const sel = $('device');
    sel.innerHTML = devices.length ? devices.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('')
      : '<option value="">No tags yet – waiting for first check</option>';
    if (!devices.find((d) => d.id === deviceId)) deviceId = devices[0]?.id || null;
    if (deviceId) sel.value = deviceId;
    renderLatest();
    const p = res.poller;
    $('pollStatus').textContent = !p.enabled ? 'Automatic checking is off on this server.'
      : p.running ? 'Checking Google for new locations…'
      : p.last_error ? `Last check failed: ${p.last_error}`
      : p.last_run ? `Last checked ${ago(p.last_run)} · every ${p.interval_minutes} min` : 'Starting…';
    renderSync(res.backup);
    renderPark(res.guard);
    renderLicense(res.license);
  }

  let licensed = true;
  function openLicense() {
    if (NATIVE && NATIVE.openLicense) NATIVE.openLicense();
    else alert('Free: 1 tag, last 24h. For 2+ tags and unlimited-day history, contact the developer for a key (meshkat4u@gmail.com).');
  }
  function renderLicense(lic) {
    // Server (no license field) = full features. Only the phone app gates.
    licensed = !lic || lic.licensed !== false;
    const el = $('pro');
    if (!el) return;
    el.hidden = licensed;
    if (!licensed) { el.textContent = '🔒 Free — Upgrade'; el.onclick = openLicense; }
    // lock history ranges beyond the free window
    document.querySelectorAll('#ranges button').forEach((b) => {
      const free = b.dataset.h === 'today' || b.dataset.h === '24';
      b.classList.toggle('locked', !licensed && !free);
    });
  }

  let parked = false;
  function renderPark(on) {
    parked = !!on;
    const el = $('park');
    if (!el) return;
    el.classList.toggle('armed', parked);
    el.classList.toggle('riding', !parked);
    el.textContent = parked ? '🔒 Parked & guarded — tap when you ride' : '🅿️ Park & guard';
  }
  function renderSync(b) {
    const el = $('sync');
    if (!el) return;
    el.classList.remove('ok', 'busy', 'err', 'off');
    if (!b || !b.enabled) {
      el.classList.add('off'); el.textContent = 'Drive off';
      el.title = 'Google Drive sync is off. Turn it on in setup.';
    } else if (b.last_error) {
      el.classList.add('busy'); el.textContent = 'Saved · sync pending';
      el.title = 'Your locations ARE saved on the phone. Drive sync will retry automatically. (' + b.last_error + ')';
    } else if (b.last_run) {
      el.classList.add('ok'); el.textContent = 'Synced';
      el.title = `Backed up to Google Drive ${ago(b.last_run)}` + (b.mode ? ` (${b.mode})` : '');
    } else {
      el.classList.add('busy'); el.textContent = 'Syncing…';
      el.title = 'First Drive sync in progress…';
    }
  }

  function renderLatest() {
    const d = devices.find((x) => x.id === deviceId);
    const el = $('latest');
    layers.live.clearLayers();
    if (!d) { el.innerHTML = '<span class="muted">No tag selected.</span>'; return; }
    if (d.last_ts == null) {
      el.innerHTML = `<b>${esc(d.name)}</b><br><span class="muted">No locations stored yet.</span>`
        + (d.last_error ? `<br><span class="error">${esc(d.last_error)}</span>` : '');
      return;
    }
    el.innerHTML = `<b>${esc(d.name)}</b><br>Last seen at ${fmt(d.last_ts)} <span class="muted">(${ago(d.last_ts)})</span><br>`
      + `<span id="addr" class="muted">📍 finding address…</span><br>`
      + `<span class="muted">${d.point_count.toLocaleString()} locations stored in total</span>`
      + (d.last_error ? `<br><span class="error small">${esc(d.last_error)}</span>` : '');
    addressFor(d.last_lat, d.last_lon).then((a) => {
      const ae = $('addr'); if (ae) ae.textContent = a ? '📍 ' + a : `📍 ${d.last_lat.toFixed(5)}, ${d.last_lon.toFixed(5)}`;
    });
    const ll = [d.last_lat, d.last_lon];
    if (d.last_accuracy) L.circle(ll, { radius: d.last_accuracy, color: d.color, weight: 1, fillOpacity: 0.12 }).addTo(layers.live);
    L.circleMarker(ll, { radius: 9, color: '#fff', weight: 3, fillColor: d.color, fillOpacity: 1 })
      .bindPopup(`<div class="popup"><b>${esc(d.name)} – latest</b>${fmt(d.last_ts)}<br>`
        + `<a target="_blank" rel="noopener" href="https://www.google.com/maps?q=${d.last_lat},${d.last_lon}">Open in Google Maps</a></div>`)
      .addTo(layers.live);
  }

  // ---------- history ----------
  async function loadHistory() {
    stopPlay();
    layers.track.clearLayers(); layers.points.clearLayers(); layers.play.clearLayers();
    if (!deviceId) { points = []; renderStats(); return; }
    const [start, end] = currentRange();
    const res = await data.history(deviceId, start, end);
    points = res.points.filter((p) => p.lat != null);
    const color = devices.find((d) => d.id === deviceId)?.color || '#2563eb';
    const lls = points.map((p) => [p.lat, p.lon]);
    if (lls.length > 1) L.polyline(lls, { color, weight: 3, opacity: 0.75 }).addTo(layers.track);
    if ($('showPoints').checked) {
      points.forEach((p, i) => {
        L.circleMarker([p.lat, p.lon], { radius: 4, color, weight: 1, fillColor: i === 0 ? '#16a34a' : '#fff', fillOpacity: 1 })
          .bindPopup(`<div class="popup"><b>${fmt(p.ts)}</b>${p.accuracy ? `± ${Math.round(p.accuracy)} m<br>` : ''}`
            + `${p.is_own_report ? 'Reported by your own phone' : 'Reported by Find Hub network'}<br>`
            + `<span class="muted">${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}</span></div>`)
          .addTo(layers.points);
      });
    }
    if (!fitted) {
      const bounds = lls.length ? L.latLngBounds(lls) : null;
      const d = devices.find((x) => x.id === deviceId);
      if (bounds) map.fitBounds(bounds.pad(0.15), { maxZoom: 16 });
      else if (d?.last_lat != null) map.setView([d.last_lat, d.last_lon], 15);
      fitted = true;
    }
    const scrub = $('scrub');
    scrub.max = Math.max(0, points.length - 1); scrub.value = scrub.max;
    $('scrubLabel').innerHTML = '&nbsp;';
    renderStats();
  }

  function renderStats() {
    let dist = 0;
    for (let i = 1; i < points.length; i++) dist += km(points[i - 1], points[i]);
    const span = points.length ? points[points.length - 1].ts - points[0].ts : 0;
    const days = span / 86400;
    $('stats').innerHTML = [
      [points.length.toLocaleString(), 'locations'],
      [dist < 10 ? dist.toFixed(2) : Math.round(dist).toLocaleString(), 'km travelled'],
      [days >= 1 ? `${days.toFixed(days < 10 ? 1 : 0)} d` : `${Math.round(span / 3600)} h`, 'time span'],
    ].map(([v, k]) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');
  }

  // ---------- playback ----------
  function showAt(i) {
    layers.play.clearLayers();
    const p = points[i];
    if (!p) return;
    const color = devices.find((d) => d.id === deviceId)?.color || '#2563eb';
    L.polyline(points.slice(0, i + 1).map((q) => [q.lat, q.lon]), { color: '#f59e0b', weight: 5, opacity: 0.9 }).addTo(layers.play);
    L.circleMarker([p.lat, p.lon], { radius: 10, color: '#fff', weight: 3, fillColor: color, fillOpacity: 1 }).addTo(layers.play);
    $('scrubLabel').textContent = `${i + 1} / ${points.length} · ${fmt(p.ts)}`;
    if (!map.getBounds().contains([p.lat, p.lon])) map.panTo([p.lat, p.lon]);
  }
  function stopPlay() { clearInterval(playTimer); playTimer = null; $('play').textContent = '▶'; }
  $('scrub').addEventListener('input', (e) => { stopPlay(); showAt(Number(e.target.value)); });
  $('play').addEventListener('click', () => {
    if (playTimer) return stopPlay();
    if (!points.length) return;
    const scrub = $('scrub');
    if (Number(scrub.value) >= points.length - 1) scrub.value = 0;
    $('play').textContent = '❚❚';
    const stepMs = Math.max(15, Math.min(300, 20000 / points.length));
    playTimer = setInterval(() => {
      const i = Number(scrub.value) + 1;
      if (i >= points.length) return stopPlay();
      scrub.value = i; showAt(i);
    }, stepMs);
  });

  // ---------- controls ----------
  function markRange() {
    document.querySelectorAll('#ranges button').forEach((b) => b.classList.toggle('on', b.dataset.h === range.h));
    $('custom').hidden = range.h !== 'custom';
  }
  $('ranges').addEventListener('click', (e) => {
    const h = e.target.dataset?.h; if (!h) return;
    if (!licensed && !(h === 'today' || h === '24')) { openLicense(); return; }
    range = { h }; markRange();
    if (h === 'custom') {
      const toLocal = (d) => new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      $('to').value ||= toLocal(new Date()); $('from').value ||= toLocal(new Date(Date.now() - 7 * 86400000));
      return;
    }
    store.set('range', h); fitted = false; loadHistory().catch(alert);
  });
  $('applyCustom').addEventListener('click', () => {
    const s = new Date($('from').value), en = new Date($('to').value);
    if (isNaN(s) || isNaN(en) || s >= en) return alert('Pick a valid date range.');
    range = { h: 'custom', start: Math.floor(s / 1000), end: Math.floor(en / 1000) };
    fitted = false; loadHistory().catch(alert);
  });
  $('device').addEventListener('change', (e) => {
    const d = devices.find((x) => x.id === e.target.value);
    if (d && d.locked) { openLicense(); e.target.value = deviceId; return; }
    deviceId = e.target.value; store.set('device', deviceId); fitted = false;
    renderLatest(); loadHistory().catch(alert);
  });
  $('showPoints').addEventListener('change', () => loadHistory().catch(alert));
  if ($('onlineAddress')) {
    $('onlineAddress').checked = store.get('allowAddressLookup') === '1';
    $('onlineAddress').addEventListener('change', (e) => {
      store.set('allowAddressLookup', e.target.checked ? '1' : '0');
      renderLatest();
    });
  }
  $('toggle').addEventListener('click', () => $('panel').classList.toggle('collapsed'));
  $('pollNow').addEventListener('click', async () => {
    try { await data.pollNow(); $('pollStatus').textContent = 'Checking Google for new locations…'; setTimeout(refresh, 15000); }
    catch (e) { alert(e.message); }
  });
  const download = (f) => {
    if (!deviceId) return;
    if (!licensed) { openLicense(); return; }
    const [start, end] = currentRange();
    if (NATIVE) NATIVE.export(deviceId, start, end, f);
    else location.href = `/api/history/${encodeURIComponent(deviceId)}?start=${start}&end=${end}&format=${f}`;
  };
  $('csv').addEventListener('click', () => download('csv'));
  $('gpx').addEventListener('click', () => download('gpx'));

  async function locateNow() {
    if (!deviceId) return;
    const btn = $('locate');
    btn.disabled = true; btn.textContent = '📍 Locating…';
    $('pollStatus').textContent = 'Asking the tag for its current location…';
    const done = () => { btn.disabled = false; btn.textContent = '📍 Locate now'; };
    try {
      await data.locate(deviceId);
    } catch (e) { $('pollStatus').textContent = e.message; done(); return; }
    if (NATIVE) {
      // The app fetches in the background and returns at once; poll for the saved point.
      let t = 0;
      const iv = setInterval(async () => { await refresh(); if (++t >= 8) { clearInterval(iv); done(); } }, 4000);
    } else {
      await refresh(); done();
    }
  }
  $('park').addEventListener('click', async () => {
    if (!licensed) { openLicense(); return; }  // theft/guard alerts are a Pro (key) feature
    const next = !parked;
    renderPark(next); // optimistic
    try { await data.setParked(next); } catch (e) { alert(e.message); }
    $('pollStatus').textContent = next
      ? 'Guarding: you’ll be alerted if the tag moves.'
      : 'Riding mode: movement alerts are off.';
    refresh();
  });
  $('locate').addEventListener('click', locateNow);
  $('ring').addEventListener('click', async () => {
    if (!deviceId) return;
    try { await data.sound(deviceId); $('pollStatus').textContent = 'Ringing the tag…'; }
    catch (e) { alert(e.message); }
  });
  $('share').addEventListener('click', async () => {
    const d = devices.find((x) => x.id === deviceId);
    if (!d || d.last_lat == null) { alert('No location yet to share.'); return; }
    const addr = await addressFor(d.last_lat, d.last_lon);
    const text = `${d.name} — last seen ${fmt(d.last_ts)}\n${addr ? addr + '\n' : ''}`
      + `https://www.google.com/maps?q=${d.last_lat},${d.last_lon}`;
    if (NATIVE && NATIVE.share) NATIVE.share(text);
    else if (navigator.share) { try { await navigator.share({ title: d.name, text }); } catch (e) {} }
    else { try { await navigator.clipboard.writeText(text); alert('Location copied to clipboard'); } catch (e) { prompt('Copy:', text); } }
  });
  $('rename').addEventListener('click', async () => {
    const d = devices.find((x) => x.id === deviceId); if (!d) return;
    const name = prompt('New name for this tag', d.name); if (!name) return;
    await data.rename(d.id, name);
    refresh();
  });
  if (window.TagTrackerApp) {
    $('server').hidden = false;
    $('server').addEventListener('click', () => window.TagTrackerApp.changeServer());
  }
  if (NATIVE) {
    // In the standalone app there is no login; "Sign out" becomes "Settings".
    $('logout').textContent = 'Settings';
    $('logout').addEventListener('click', () => NATIVE.openSettings());
  } else {
    $('logout').addEventListener('click', async () => { await api('/api/logout', { method: 'POST' }); location.href = '/login'; });
  }

  async function refresh() {
    try { await loadDevices(); if (!playTimer) await loadHistory(); } catch (e) { console.error(e); }
  }
  setInterval(() => { if ($('autoRefresh').checked && !document.hidden) refresh(); }, 60000);

  if (range.h === 'custom') range.h = '168';
  markRange();
  refresh();
})();
