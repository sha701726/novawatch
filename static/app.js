const $ = (id) => document.getElementById(id);

// ── Password eye toggle ───────────────────────────────────────────────────────
function togglePass(id, btn) {
  const inp = $(id);
  if (!inp) return;
  const show = inp.type === 'password';
  inp.type = show ? 'text' : 'password';
  const closed = btn.querySelector('.eye-closed');
  const open   = btn.querySelector('.eye-open');
  if (closed && open) {
    closed.style.display = show ? 'none'  : 'block';
    open.style.display   = show ? 'block' : 'none';
  } else {
    btn.style.opacity = show ? '1' : '0.4';
  }
}

// ── Image lightbox ────────────────────────────────────────────────────────────
function openLightbox(src) {
  const lb = $('imgLightbox');
  const img = $('imgLightboxSrc');
  if (!lb || !img || !src) return;
  img.src = src;
  lb.classList.remove('hidden');
}
function closeLightbox(e) {
  if (e.target === $('imgLightbox')) $('imgLightbox').classList.add('hidden');
}

// ── Image upload modal ────────────────────────────────────────────────────────
let _pendingImgUrl = '';   // holds final URL (either typed or uploaded)

function openImgUploadModal() {
  $('imgUploadModal')?.classList.remove('hidden');
  const current = $('editImageUrl')?.value || '';
  $('imgUrlInput').value = current;
  _pendingImgUrl = current;
  if (current) previewImgUrl();
  else { const pv = $('imgUrlPreview'); if (pv) pv.style.display = 'none'; }
  setMsg($('imgUploadMsg'), '');
}

function switchImgTab(tab) {
  $('imgTabUrl').style.display = tab === 'url' ? '' : 'none';
  $('imgTabFile').style.display = tab === 'file' ? '' : 'none';
  document.querySelectorAll('.img-source-tab').forEach(b => b.classList.remove('active'));
  $(tab === 'url' ? 'tabUrlBtn' : 'tabFileBtn')?.classList.add('active');
}

function previewImgUrl() {
  const url = $('imgUrlInput')?.value.trim();
  const pv = $('imgUrlPreview');
  const img = $('imgUrlPreviewImg');
  if (url && pv && img) { img.src = url; pv.style.display = ''; _pendingImgUrl = url; }
  else if (pv) pv.style.display = 'none';
}

function handleImgDrop(e) {
  e.preventDefault();
  $('imgDropZone').style.borderColor = 'rgba(212,175,55,0.4)';
  const file = e.dataTransfer?.files?.[0];
  if (file) onImgFileSelected(file);
}

function onImgFileSelected(file) {
  if (!file) return;
  const allowed = ['image/png','image/jpeg','image/jpg','image/gif','image/webp'];
  if (!allowed.includes(file.type)) {
    setMsg($('imgUploadMsg'), 'Only PNG, JPG, GIF, WEBP allowed'); return;
  }
  if (file.size > 10 * 1024 * 1024) {
    setMsg($('imgUploadMsg'), 'File too large. Max 10MB allowed.'); return;
  }
  // Show local preview
  const reader = new FileReader();
  reader.onload = e2 => {
    const pv = $('imgFilePreview');
    const img = $('imgFilePreviewImg');
    const fn = $('imgFileName');
    if (img) img.src = e2.target.result;
    if (fn) fn.textContent = file.name + ' (' + (file.size/1024).toFixed(1) + ' KB)';
    if (pv) pv.style.display = '';
  };
  reader.readAsDataURL(file);
  // Upload to server
  uploadImgToServer(file);
}

async function uploadImgToServer(file) {
  const prog = $('imgUploadProgress');
  const bar  = $('imgProgressBar');
  const txt  = $('imgProgressText');
  const btn  = $('btnUseImage');
  const msg  = $('imgUploadMsg');

  if (prog) prog.style.display = '';
  if (bar)  bar.style.width = '10%';
  if (txt)  txt.textContent = 'Uploading...';
  if (btn)  btn.disabled = true;
  setMsg(msg, '');

  const formData = new FormData();
  formData.append('file', file);

  try {
    // Fake progress animation while uploading
    let pct = 10;
    const ticker = setInterval(() => {
      pct = Math.min(pct + 8, 85);
      if (bar) bar.style.width = pct + '%';
    }, 200);

    const res = await fetch('/api/admin/upload-image', {
      method: 'POST',
      body: formData,
      credentials: 'include'
    });
    clearInterval(ticker);
    const data = await res.json();

    if (!res.ok || data.error) {
      if (bar)  bar.style.width = '0%';
      if (prog) prog.style.display = 'none';
      if (btn)  btn.disabled = false;
      setMsg(msg, '' + (data.error || 'Upload failed'));
      return;
    }

    if (bar)  bar.style.width = '100%';
    if (txt)  txt.textContent = '✓ Uploaded!';
    _pendingImgUrl = data.url;
    // Also fill URL tab to show it
    if ($('imgUrlInput')) $('imgUrlInput').value = data.url;
    previewImgUrl();
    setTimeout(() => { if (prog) prog.style.display = 'none'; if (bar) bar.style.width = '0%'; }, 800);
    if (btn) btn.disabled = false;
    setMsg(msg, '✓ Image uploaded successfully!', 'ok');
  } catch(e) {
    if (prog) prog.style.display = 'none';
    if (btn)  btn.disabled = false;
    setMsg(msg, 'Upload failed: ' + e.message);
  }
}

function previewImgFile() {} // legacy stub - not used anymore

function applyImgUrl() {
  const url = (_pendingImgUrl || $('imgUrlInput')?.value || '').trim();
  if (!url) { setMsg($('imgUploadMsg'), 'Please select an image or enter a URL'); return; }
  const editUrl = $('editImageUrl');
  if (editUrl) {
    editUrl.value = url;
    const pv = $('editImgPreview');
    const img = $('editImgPreviewImg');
    if (img) { img.src = url; if (pv) pv.style.display = ''; }
  }
  $('imgUploadModal')?.classList.add('hidden');
}

// ── Map address picker ────────────────────────────────────────────────────────
function openMapPicker(addrInputId) {
  // Opens OSM nominatim search in a simple inline modal
  const existingModal = $('mapPickerModal');
  if (existingModal) existingModal.remove();

  const modal = document.createElement('div');
  modal.id = 'mapPickerModal';
  modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.85);display:flex;flex-direction:column;align-items:stretch;padding:16px;box-sizing:border-box;';
  modal.innerHTML = `
    <div style="background:#1a1a1a;border:1px solid rgba(212,175,55,0.2);border-radius:10px;overflow:hidden;display:flex;flex-direction:column;max-height:100%;flex:1;">
      <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-bottom:1px solid rgba(255,255,255,0.08);">
        <div style="font-weight:600;color:var(--gold2);">Choose Location on Map</div>
        <button onclick="document.getElementById('mapPickerModal').remove()" style="background:none;border:none;color:#aaa;font-size:20px;cursor:pointer;">✕</button>
      </div>
      <div style="padding:10px 14px;display:flex;gap:6px;">
        <input id="mapSearchInput" placeholder="Search for your address..." style="flex:1;padding:8px 10px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.15);border-radius:6px;color:#fff;font-size:13px;" />
        <button onclick="searchMapLocation()" style="padding:8px 14px;background:var(--gold2);color:#000;border:none;border-radius:6px;font-weight:600;cursor:pointer;">Search</button>
      </div>
      <div id="mapSearchResults" style="padding:0 14px;max-height:180px;overflow-y:auto;"></div>
      <iframe id="mapIframe" src="https://www.openstreetmap.org/export/embed.html?bbox=68,8,98,38&layer=mapnik" style="flex:1;min-height:220px;border:none;" allowfullscreen></iframe>
      <div style="padding:10px 14px;border-top:1px solid rgba(255,255,255,0.08);">
        <div id="mapSelectedAddr" class="muted small" style="margin-bottom:8px;">Search and select an address above</div>
        <button id="mapConfirmBtn" onclick="confirmMapAddress('${addrInputId}')" style="width:100%;padding:10px;background:var(--gold2);color:#000;border:none;border-radius:6px;font-weight:700;cursor:pointer;opacity:0.5;" disabled>Use This Address</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal._targetInputId = addrInputId;
  modal._selectedAddress = '';
  setTimeout(() => $('mapSearchInput')?.focus(), 100);
}

async function searchMapLocation() {
  const q = $('mapSearchInput')?.value.trim();
  if (!q) return;
  const res = document.getElementById('mapSearchResults');
  if (res) res.innerHTML = '<div class="muted small" style="padding:8px 0;">Searching...</div>';
  try {
    const r = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(q)}&format=json&limit=5&countrycodes=in`, {headers:{'Accept-Language':'en'}});
    const data = await r.json();
    if (!res) return;
    if (!data.length) { res.innerHTML = '<div class="muted small" style="padding:8px 0;">No results found</div>'; return; }
    res.innerHTML = data.map((d,i) => `<div onclick="selectMapResult(${i},'${encodeURIComponent(d.display_name)}',${d.lat},${d.lon})" style="padding:8px 6px;border-bottom:1px solid rgba(255,255,255,0.06);cursor:pointer;font-size:12px;color:#ddd;" onmouseover="this.style.background='rgba(212,175,55,0.1)'" onmouseout="this.style.background='none'">${d.display_name}</div>`).join('');
    window._mapResults = data;
  } catch(e) {
    if (res) res.innerHTML = '<div class="muted small" style="padding:8px 0;color:var(--danger);">Search failed</div>';
  }
}

function selectMapResult(i, encodedAddr, lat, lon) {
  const addr = decodeURIComponent(encodedAddr);
  const modal = $('mapPickerModal');
  if (!modal) return;
  modal._selectedAddress = addr;
  const sel = $('mapSelectedAddr');
  if (sel) { sel.textContent = 'Location' + addr; sel.style.color = 'var(--gold2)'; }
  const btn = $('mapConfirmBtn');
  if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
  const iframe = $('mapIframe');
  if (iframe) iframe.src = `https://www.openstreetmap.org/export/embed.html?bbox=${lon-0.05},${lat-0.05},${lon+0.05},${lat+0.05}&layer=mapnik&marker=${lat},${lon}`;
  const res = $('mapSearchResults');
  if (res) res.innerHTML = '';
}

function confirmMapAddress(inputId) {
  const modal = $('mapPickerModal');
  if (!modal || !modal._selectedAddress) return;
  const inp = $(inputId);
  if (inp) inp.value = modal._selectedAddress;
  modal.remove();
}



let screens = {};

const state = {
  me: null,
  receiver: null,
  authCheckVersion: 0,
  camera: { stream: null, raf: null, active: false, scanStartTime: null },
  activity: { timer: null },
  run: { active: false, watchId: null, points: [], lastPos: null, totalKm: 0, currentSpeed: 0 },
  buy: { selectedCoins: 0, selectedPrice: 0 },
};

// ── SCREEN MANAGEMENT ─────────────────────────────────────────────────────
function showScreen(name) {
  // Close chat view if open when navigating away
  if (name !== 'connect' && _chatPeerId) closeChatView();
  // Stop bg poll when leaving connect screen
  if (name !== 'connect') stopBgPoll();
  // Stop dashboard activity polling when leaving dashboard
  if (name !== 'dashboard') stopDashActivityPolling();
  Object.entries(screens).forEach(([key, el]) => {
    if (!el) return;
    if (key === name) el.classList.remove('hidden');
    else el.classList.add('hidden');
  });
  // Persist current screen so refresh restores it
  try { localStorage.setItem('nova_screen', name); } catch {}
}

function showSplash(visible) {
  const splash = $('splash');
  if (!splash) return;
  if (visible) splash.classList.remove('hidden');
  else splash.classList.add('hidden');
}

function normalizeCode(s) { return (s || '').toString().trim().toUpperCase(); }

function setMsg(el, msg, kind = 'error') {
  if (!el) return;
  el.textContent = msg || '';
  el.style.color = kind === 'ok' ? 'var(--ok)' : 'var(--danger)';
}

async function api(path, method = 'GET', body) {
  const opts = { method, headers: {}, credentials: 'include' };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = data && data.error ? data.error : `Request failed (${res.status})`;
    throw new Error(err);
  }
  return data;
}

// ── SPLASH LOGO CLICK ─────────────────────────────────────────────────────
function initSplash() {
  const btn = $('splashLogoBtn');
  const loader = $('splashLoader');
  const hint = $('splashHint');
  if (!btn) return;

  // After initial check for session, show tap hint if not logged in
  window._splashTapReady = false;

  btn.addEventListener('click', () => {
    if (!window._splashTapReady) return; // not ready yet (still checking session)
    // Animate then reveal auth
    btn.style.transform = 'scale(0.9)';
    setTimeout(() => { btn.style.transform = ''; }, 150);
    setTimeout(() => {
      showSplash(false);
      showScreen('auth');
    }, 220);
  });

  btn.style.transition = 'transform 0.18s ease';
}

// ── QR ────────────────────────────────────────────────────────────────────

function renderQr(boxId, qrText) {
  const box = $(boxId);
  if (!box) return;
  box.innerHTML = '';
  new QRCode(box, {
    text: qrText,
    width: 200,
    height: 200,
    colorDark: '#ffffff',
    colorLight: '#000000',
    correctLevel: QRCode.CorrectLevel.M,
  });
}

// ── DASHBOARD ─────────────────────────────────────────────────────────────

async function refreshDashboard(skipMeFetch) {
  // Only re-fetch /api/me if explicitly needed (avoid duplicate call on login)
  if (!skipMeFetch) state.me = await api('/api/me');
  const coinValue = state.me.coinvalue;
  const rupeeValue = (coinValue * 10000).toLocaleString('en-IN');
  $('meCodeName').textContent = state.me.code_name;
  $('meCoinValue').textContent = `${coinValue} (₹${rupeeValue})`;
  $('meCoin').textContent = String(state.me.coin);
  $('sidebarMeCodeName').textContent = state.me.code_name;
  $('sidebarMeCoinValue').textContent = `${coinValue} (₹${rupeeValue})`;
  $('sidebarMeCoin').textContent = state.me.coin;
  // Fetch QR and transactions in parallel
  const [qr] = await Promise.all([api('/api/qr/mine'), loadTransactions()]);
  renderQr('myQrBox', qr.qrText);
  state.myQrDeepLink = qr.deepLink || null;
  state.myQrText     = qr.qrText  || null;
  // Start live activity feed on dashboard
  startDashActivityPolling();
}

async function loadTransactions() {
  const txRes = await api('/api/transactions');
  const list = $('txList');
  if (!list) return;
  list.innerHTML = '';
  const txs = txRes.transactions || [];
  if (!txs.length) {
    list.innerHTML = '<div class="tx-item muted">No transactions yet.</div>';
    return;
  }
  const myCode = state.me ? state.me.code_name : '';
  txs.slice(0, 4).forEach((t) => {
    const who =
      t.payer_code_name === myCode ? 'You paid' :
      t.receiver_code_name === myCode ? 'You received' : 'Transaction';
    const rupeeAmount = (t.amount * 10000).toLocaleString('en-IN');
    const div = document.createElement('div');
    div.className = 'tx-item';
    div.innerHTML = `
      <div class="tx-title">${who}</div>
      <div class="tx-sub">Amount: <span style="color:var(--gold2);font-weight:900">${t.amount} coin${t.amount !== 1 ? 's' : ''}</span> <span class="muted small">(₹${rupeeAmount})</span></div>
      <div class="tx-sub">Time: ${t.created_at}</div>
    `;
    list.appendChild(div);
  });
}

// ── After login: redirect to pending pay if deep-link was used ───────────
// ── Resolve a deep-link pay by code name (no TTL) ───────────────
async function _resolvePayLink(codeName) {
  const res = await api(`/api/pay-link/resolve?code=${encodeURIComponent(codeName)}`);
  state.receiver = {
    receiver_code_name: normalizeCode(res.receiver_code_name),
    receiver_coin:      String(res.receiver_coin).trim(),
    timestamp:          null,
  };
  showReceiverBox();
}

async function _afterLoginRedirect(me) {
  // Hide pay-pending banner if visible
  const banner = document.getElementById('payPendingBanner');
  if (banner) banner.style.display = 'none';

  if (state.pendingPayQr) {
    const codeName = state.pendingPayQr;
    state.pendingPayQr = null;
    await refreshDashboard(true);
    try {
      await _resolvePayLink(codeName);
      $('manualReceiverBox').classList.add('hidden');
      $('payAmount').value    = '';
      $('payPassword').value  = '';
      $('payMsg').textContent = '';
      showScreen('pay');
    } catch(e) {
      showScreen('dashboard');
      await refreshDashboard(true);
    }
    return true; // handled
  }
  return false; // no pending pay
}

async function checkLoginOnLoad() {
  // ── Deep-link ?pay= handler ────────────────────────────────────────
  // If URL has ?pay=<qrText>, store it and clear from URL bar cleanly
  const _urlParams  = new URLSearchParams(window.location.search);
  const _pendingPay = _urlParams.get('pay');
  if (_pendingPay) {
    // Store plain code name (deep link uses /?pay=CODENAME, no encrypted payload)
    state.pendingPayQr = _pendingPay.trim().toUpperCase();
    // Clean URL bar without reload
    window.history.replaceState({}, '', window.location.pathname);
  }
  // ──────────────────────────────────────────────────────────────────

  const v = ++state.authCheckVersion;
  try {
    const me = await api('/api/me');
    if (v !== state.authCheckVersion) return;
    showSplash(false);
    $('loginStatus').textContent = 'Login verified';
    state.me = me;

    // ── If deep-link pay pending → go directly to pay screen ─────────
    if (state.pendingPayQr) {
      const codeName = state.pendingPayQr;
      state.pendingPayQr = null;
      await refreshDashboard(true);
      try {
        await _resolvePayLink(codeName);
        // Go to pay screen directly (bypass login guard since we are logged in)
        $('manualReceiverBox').classList.add('hidden');
        $('payAmount').value    = '';
        $('payPassword').value  = '';
        $('payMsg').textContent = '';
        showScreen('pay');
      } catch(e) {
        showScreen('dashboard');
        await refreshDashboard(true);
        setTimeout(() => {
          const msg = document.getElementById('payMsg');
          if (msg) { msg.textContent = `Could not load payment: ${e.message || e}`; msg.style.color = 'var(--danger)'; }
        }, 500);
      }
      return;
    }
    // ─────────────────────────────────────────────────────────────────

    // Restore previous screen if available (page refresh persistence)
    const RESTORABLE = ['dashboard', 'home', 'profile', 'connect', 'store', 'admin'];
    let savedScreen = null;
    try { savedScreen = localStorage.getItem('nova_screen'); } catch {}

    if (savedScreen && RESTORABLE.includes(savedScreen)) {
      showScreen(savedScreen);
      // Re-initialise each screen that needs a data load
      if (savedScreen === 'dashboard') {
        await refreshDashboard(true);
      } else if (savedScreen === 'home') {
        await refreshDashboard(true);
        await openHomeScreen();
      } else if (savedScreen === 'profile') {
        await refreshDashboard(true);
        await openProfileScreen();
      } else if (savedScreen === 'connect') {
        await refreshDashboard(true);
        await openConnectScreen();
      } else if (savedScreen === 'store') {
        await refreshDashboard(true);
        await openStore();
      } else if (savedScreen === 'admin') {
        await refreshDashboard(true);
        if (isStoreAdmin()) {
          const savedTab = localStorage.getItem('nova_admin_tab') || 'products';
          openAdminPanel();
          setTimeout(() => switchAdminTab(savedTab), 100);
        } else {
          showScreen('dashboard');
          await refreshDashboard(true);
        }
      }
    } else {
      showScreen('dashboard');
      await refreshDashboard(true); // skip re-fetching /api/me — already have it
    }
  } catch {
    if (v !== state.authCheckVersion) return;
    // Not logged in — show splash/auth
    try { localStorage.removeItem('nova_screen'); } catch {}
    const loader = $('splashLoader');
    const hint   = $('splashHint');
    if (loader) loader.style.display = 'none';
    if (hint)   hint.style.display   = 'block';
    window._splashTapReady = true;

    // ── Deep-link pending: show a banner on the auth screen ──────────
    if (state.pendingPayQr) {
      // After user registers/logs in, submitPay will pick up state.pendingPayQr
      _showPayPendingBanner();
    }
    // ─────────────────────────────────────────────────────────────────
  }
}

function _showPayPendingBanner() {
  // Show a sticky banner at top of auth screen explaining why they need to sign in
  let banner = document.getElementById('payPendingBanner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'payPendingBanner';
    banner.style.cssText = [
      'position:fixed','top:0','left:0','right:0','z-index:500',
      'background:linear-gradient(90deg,rgba(212,175,55,0.18),rgba(212,175,55,0.08))',
      'border-bottom:1.5px solid rgba(212,175,55,0.45)',
      'padding:12px 16px','display:flex','align-items:center','gap:10px',
      'font-size:13px','color:#e8e8e8','line-height:1.4'
    ].join(';');
    banner.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#d4af37" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="3" height="3" rx="0.5"/><rect x="18" y="14" width="3" height="3" rx="0.5"/><rect x="14" y="18" width="3" height="3" rx="0.5"/><rect x="18" y="18" width="3" height="3" rx="0.5"/></svg>
      <span><strong style="color:#d4af37">Payment link received.</strong> Sign in or create an account to complete the payment.</span>
    `;
    document.body.appendChild(banner);
  }
  banner.style.display = 'flex';
}

function enforceUppercaseInputs() {
  ['regUserId','regCodeName','loginCodeName','searchCodeName','manualReceiverCodeName'].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('input', () => { el.value = normalizeCode(el.value); });
  });
}

// ── BUY COINS ────────────────────────────────────────────────────────────

function initBuyCoins() {
  const pkgs = document.querySelectorAll('.coin-pkg');
  pkgs.forEach((pkg) => {
    pkg.addEventListener('click', () => {
      pkgs.forEach(p => p.classList.remove('selected'));
      pkg.classList.add('selected');
      const coins = parseInt(pkg.dataset.coins);
      const price = parseInt(pkg.dataset.price); // in paise
      state.buy = { selectedCoins: coins, selectedPrice: price };
      // Show selected info
      const info = $('selectedPkgInfo');
      const txt = $('selectedPkgText');
      const priceRs = (price / 100).toLocaleString('en-IN');
      if (info && txt) {
        info.classList.remove('hidden');
        txt.textContent = `${coins} Coin${coins > 1 ? 's' : ''} for \u20B9${priceRs}`;
      }
      const btn = $('btnBuyCoins');
      if (btn) {
        btn.disabled = false;
        btn.textContent = `Buy ${coins} Coin${coins > 1 ? 's' : ''} for \u20B9${priceRs}`;
      }
    });
  });

  const clearBtn = $('btnClearPkg');
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      pkgs.forEach(p => p.classList.remove('selected'));
      state.buy = { selectedCoins: 0, selectedPrice: 0 };
      const info = $('selectedPkgInfo');
      if (info) info.classList.add('hidden');
      const btn = $('btnBuyCoins');
      if (btn) { btn.disabled = true; btn.textContent = 'Select a Package to Buy'; }
    });
  }

  const buyBtn = $('btnBuyCoins');
  if (buyBtn) {
    buyBtn.addEventListener('click', () => {
      if (!state.buy.selectedCoins) return;
      if (!state.me) { setMsg($('buyMsg'), 'Please login first.'); return; }
      launchRazorpay(state.buy.selectedCoins, state.buy.selectedPrice);
    });
  }
}

function launchRazorpay(coins, amountPaise) {
  const buyMsg = $('buyMsg');
  setMsg(buyMsg, 'Opening payment gateway...', 'ok');

  // Create Razorpay order via backend
  api('/api/payment/create-order', 'POST', { coins, amount: amountPaise })
    .then((order) => {
      const options = {
        key: 'rzp_test_SiOpSGdjiLhfLU',
        amount: order.amount,
        currency: 'INR',
        name: 'Nova Coins',
        description: `${coins} Nova Coin${coins > 1 ? 's' : ''}`,
        order_id: order.razorpay_order_id,
        handler: function(response) {
          // Payment captured - verify and credit coins
          verifyAndCreditCoins(response, coins, amountPaise);
        },
        prefill: {
          name: state.me ? state.me.code_name : '',
          contact: '',
        },
        notes: {
          code_name: state.me ? state.me.code_name : '',
          coins: String(coins),
        },
        theme: { color: '#d4af37' },
        modal: {
          ondismiss: function() {
            setMsg(buyMsg, 'Payment cancelled.');
          }
        }
      };
      const rzp = new Razorpay(options);
      rzp.on('payment.failed', function(resp) {
        setMsg(buyMsg, 'Payment failed: ' + (resp.error.description || 'Unknown error'));
      });
      setMsg(buyMsg, '');
      rzp.open();
    })
    .catch((e) => {
      setMsg(buyMsg, 'Could not initiate payment: ' + (e.message || e));
    });
}

async function verifyAndCreditCoins(rzpResponse, coins, amountPaise) {
  const buyMsg = $('buyMsg');
  setMsg(buyMsg, 'Verifying payment & crediting coins...', 'ok');
  try {
    const res = await api('/api/payment/verify', 'POST', {
      razorpay_order_id:   rzpResponse.razorpay_order_id,
      razorpay_payment_id: rzpResponse.razorpay_payment_id,
      razorpay_signature:  rzpResponse.razorpay_signature,
      coins:  coins,
      amount: amountPaise,
    });
    if (res.ok) {
      const rupeeAdded = (coins * 10000).toLocaleString('en-IN');
      setMsg(buyMsg, `${coins} coins (Rs ${rupeeAdded}) credited! New balance: ${res.new_balance}`, 'ok');
      await refreshDashboard();
    } else {
      setMsg(buyMsg, res.error || 'Verification failed.');
    }
  } catch(e) {
    setMsg(buyMsg, 'Credit failed: ' + (e.message || e));
  }
}

// ── INVOICE ───────────────────────────────────────────────────────────────

// ── SCANNER ───────────────────────────────────────────────────────────────
function openScannerModal() {
  const modal = $("scannerModal");
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  $("scannerMsg").textContent = "Align QR code within the frame";
  const pasteEl = $("qrPaste");
  if (pasteEl) { pasteEl.value = ""; }
  const searchInp = $("scannerSearchInput");
  if (searchInp) searchInp.value = "";
  state.camera.scanStartTime = null;
  state.camera.torchOn = false;
  const tOff = $("torchIconOff"); const tOn = $("torchIconOn");
  if (tOff) tOff.style.display = ""; if (tOn) tOn.style.display = "none";
  stopCamera();
  startCamera();
}

function closeScannerModal() {
  const modal = $("scannerModal");
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  if (state.camera.torchTrack) {
    try { state.camera.torchTrack.applyConstraints({ advanced: [{ torch: false }] }); } catch {}
    state.camera.torchTrack = null;
  }
  state.camera.torchOn = false;
  stopCamera();
}

async function toggleTorch() {
  const stream = state.camera.stream;
  if (!stream) return;
  const videoTrack = stream.getVideoTracks()[0];
  if (!videoTrack) return;
  state.camera.torchOn = !state.camera.torchOn;
  try {
    await videoTrack.applyConstraints({ advanced: [{ torch: state.camera.torchOn }] });
    state.camera.torchTrack = videoTrack;
    const tOff = $("torchIconOff"); const tOn = $("torchIconOn");
    if (tOff) tOff.style.display = state.camera.torchOn ? "none" : "";
    if (tOn)  tOn.style.display  = state.camera.torchOn ? "" : "none";
  } catch {
    state.camera.torchOn = false;
    $("scannerMsg").textContent = "Torch not supported on this device.";
  }
}

async function scanFromGallery(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = img.width; canvas.height = img.height;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0);
      const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      if (typeof jsQR !== "function") { $("scannerMsg").textContent = "QR decoder not loaded."; return; }
      const code = jsQR(imgData.data, imgData.width, imgData.height, { inversionAttempts: "attemptBoth" });
      if (code && code.data) {
        $("qrPaste").value = code.data.trim();
        $("scannerMsg").textContent = "QR detected from image! Verifying...";
        $("scannerMsg").style.color = "var(--ok)";
        verifyQrTextAndGoPay(code.data.trim());
      } else {
        $("scannerMsg").textContent = "No QR found in the image. Try another.";
        $("scannerMsg").style.color = "var(--danger)";
      }
    };
    img.src = e.target.result;
  };
  reader.readAsDataURL(file);
}

function openAboutModal() {
  const modal = $('aboutModal');
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  startActivityPolling();
}

function closeAboutModal() {
  const modal = $('aboutModal');
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  stopActivityPolling();
}

function openHistoryModal() {
  const modal = $('historyModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  loadTransactions();
}

function closeHistoryModal() {
  const modal = $('historyModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
}

function stopActivityPolling() {
  if (state.activity.timer) clearInterval(state.activity.timer);
  state.activity.timer = null;
}

// Renders activity into a given list element id
async function renderActivityInto(listId) {
  const list = $(listId);
  if (!list) return;
  try {
    const txRes = await api('/api/activity');
    const txs = txRes.activity || [];
    list.innerHTML = '';
    if (!txs.length) {
      list.innerHTML = '<div class="muted small">No activity yet.</div>';
      return;
    }
    txs.slice(0, 4).forEach((t) => {
      const div = document.createElement('div');
      div.className = listId === 'dashActivityList' ? 'dash-activity-item' : 'activity-item';
      div.innerHTML = `
        <div class="${listId === 'dashActivityList' ? 'dash-activity-line' : 'activity-line'}">${t.payer_code_name} &rarr; ${t.receiver_code_name}</div>
        <div class="${listId === 'dashActivityList' ? 'dash-activity-time' : 'activity-time'}">${t.created_at}</div>
      `;
      list.appendChild(div);
    });
  } catch (e) {
    list.innerHTML = `<div class="muted small" style="color:var(--danger)">${e.message || e}</div>`;
  }
}

async function renderActivityOnce() {
  await renderActivityInto('activityList');
}

// Dashboard activity auto-refresh timer (separate from modal polling)
const _dashActivity = { timer: null };

function startDashActivityPolling() {
  stopDashActivityPolling();
  renderActivityInto('dashActivityList');
  _dashActivity.timer = setInterval(() => renderActivityInto('dashActivityList'), 10000);
}

function stopDashActivityPolling() {
  if (_dashActivity.timer) clearInterval(_dashActivity.timer);
  _dashActivity.timer = null;
}

function startActivityPolling() {
  stopActivityPolling();
  renderActivityOnce();
  state.activity.timer = setInterval(renderActivityOnce, 3000);
}

// ── CAMERA ────────────────────────────────────────────────────────────────

function stopCamera() {
  const cam = state.camera;
  if (cam.raf) cancelAnimationFrame(cam.raf);
  cam.raf = null;
  cam.active = false;
  if (cam.stream) { cam.stream.getTracks().forEach((t) => t.stop()); cam.stream = null; }
  // Stop scan line animation when scanning ends
  const scanLine = document.querySelector('.nsf-scan-line');
  if (scanLine) scanLine.style.animationPlayState = 'paused';
}

async function startCamera() {
  const video = $('scannerVideo');
  const canvas = $('scannerCanvas');
  // Resume scan line animation when scanning starts
  const scanLine = document.querySelector('.nsf-scan-line');
  if (scanLine) scanLine.style.animationPlayState = 'running';
  // Smaller canvas = fewer pixels = faster jsQR decode
  const SCAN_SIZE = 300;
  const offCanvas = document.createElement('canvas');
  offCanvas.width = SCAN_SIZE; offCanvas.height = SCAN_SIZE;
  const offCtx = offCanvas.getContext('2d', { willReadFrequently: true });
  try {
    const isSecure = window.isSecureContext || location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
    if (!isSecure) { $('scannerMsg').textContent = 'Camera requires HTTPS. Use "Paste QR text" below.'; return; }
    // Request a modest resolution — enough for QR, much faster to decode
    const constraints = {
      video: {
        facingMode: { ideal: 'environment' },
        width:  { ideal: 640 },
        height: { ideal: 480 }
      },
      audio: false
    };
    let stream = null;
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      stream = await navigator.mediaDevices.getUserMedia(constraints);
    } else {
      const legacy = navigator.getUserMedia || navigator.webkitGetUserMedia || navigator.mozGetUserMedia;
      if (!legacy) { $('scannerMsg').textContent = 'Camera not supported. Paste QR text below.'; return; }
      stream = await new Promise((resolve, reject) => { legacy.call(navigator, constraints, resolve, reject); });
    }
    state.camera.stream = stream;
    state.camera.active = true;
    video.muted = true;
    video.srcObject = stream;
    // Wait for metadata AND first frame data to be ready
    await new Promise((resolve) => {
      if (video.readyState >= 3) { resolve(); return; }
      video.onloadeddata = () => resolve();
      video.onloadedmetadata = () => { if (video.readyState >= 3) resolve(); };
    });
    await video.play().catch(() => {});
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    $('scannerCanvas').classList.add('hidden');
    if (!state.camera.scanStartTime) state.camera.scanStartTime = Date.now();

    // Frame skip counter — decode every 2nd rAF (~30fps) to reduce CPU load
    let _frameCount = 0;
    const scan = () => {
      if (!state.camera.active) return;
      if (Date.now() - state.camera.scanStartTime > 60000) {
        stopCamera();
        $('scannerMsg').textContent = 'QR scan timed out. Paste QR text below.';
        return;
      }
      if (typeof jsQR !== 'function') { stopCamera(); $('scannerMsg').textContent = 'QR decoder not loaded. Refresh page.'; return; }
      if (video.readyState < 2 || video.paused) { state.camera.raf = requestAnimationFrame(scan); return; }
      // Skip every other frame — camera delivers fresh frame, CPU gets breathing room
      _frameCount++;
      if (_frameCount % 2 !== 0) { state.camera.raf = requestAnimationFrame(scan); return; }
      // Draw downscaled frame — jsQR is much faster on 300x300 vs 640x480
      offCtx.drawImage(video, 0, 0, SCAN_SIZE, SCAN_SIZE);
      const img = offCtx.getImageData(0, 0, SCAN_SIZE, SCAN_SIZE);
      // attemptBoth handles normal AND inverted/dark-bg QR codes
      const code = jsQR(img.data, img.width, img.height, { inversionAttempts: 'attemptBoth' });
      if (code && code.data) {
        const qrText = String(code.data).trim();
        state.camera.active = false;
        if (state.camera.raf) cancelAnimationFrame(state.camera.raf);
        state.camera.raf = null;
        stopCamera();
        const pasteEl = $('qrPaste');
        pasteEl.value = qrText;
        pasteEl.style.borderColor = 'var(--ok)';
        $('scannerMsg').textContent = 'QR detected! Verifying automatically...';
        $('scannerMsg').style.color = 'var(--ok)';
        verifyQrTextAndGoPay(qrText);
        return;
      }
      state.camera.raf = requestAnimationFrame(scan);
    };
    state.camera.raf = requestAnimationFrame(scan);
  } catch (e) {
    const msg = (e && e.name) ? `${e.name}: ${e.message || ''}` : (e?.message || String(e));
    $('scannerMsg').textContent = `Camera error: ${msg}. Allow permission or paste QR text.`;
  }
}

async function verifyQrTextAndGoPay(qrText) {
  try {
    $('scannerMsg').textContent = 'Verifying QR...';
    $('scannerMsg').style.color = 'var(--muted)';
    const verify = await api('/api/qr/verify', 'POST', { qrText });
    closeScannerModal();
    state.receiver = {
      receiver_code_name: normalizeCode(verify.receiver_code_name || verify.code_name),
      receiver_coin: String(verify.receiver_coin || verify.coin || '').trim(),
      timestamp: verify.timestamp,
    };
    showReceiverBox();
    showPayScreenWithScan();
  } catch (e) {
    $('scannerMsg').textContent = `QR verify failed: ${e.message || e}`;
    $('scannerMsg').style.color = 'var(--danger)';
  }
}

function showReceiverBox() {
  const box = $('receiverBox');
  if (!state.receiver) { box.classList.add('hidden'); box.innerHTML = ''; return; }
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="receiver-line">Receiver: <strong>${state.receiver.receiver_code_name}</strong></div>
    <div class="receiver-line">Account no: ${state.receiver.receiver_coin}</div>
    ${state.receiver.timestamp ? `<div class="muted small" style="margin-top:8px;">QR time: ${new Date(state.receiver.timestamp * 1000).toLocaleString()}</div>` : ''}
  `;
}

function showPayScreenWithScan() {
  if (!state.me) { showAuthRequiredToast('pay'); return; }
  $('manualReceiverBox').classList.add('hidden');
  $('payAmount').value = '';
  $('payPassword').value = '';
  $('payMsg').textContent = '';
  showScreen('pay');
}

function showPayScreenWithManual() {
  if (!state.me) { showAuthRequiredToast('pay'); return; }
  state.receiver = null;
  $('receiverBox').classList.add('hidden');
  $('manualReceiverCoin').value = '';
  $('payAmount').value = '';
  $('payPassword').value = '';
  $('payMsg').textContent = '';
  $('manualReceiverBox').classList.remove('hidden');
  showScreen('pay');
}

function showAuthRequiredToast(context) {
  // Show a brief toast/alert directing user to login
  const msg = context === 'pay'
    ? 'Please create an account or sign in to make payments.'
    : 'Please sign in to continue.';
  // Try to use existing msg elements, else use alert
  const payMsg = document.getElementById('payMsg');
  if (payMsg) {
    payMsg.textContent = msg;
    payMsg.style.color = 'var(--danger)';
  }
  // Show a floating toast
  let toast = document.getElementById('novaAuthToast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'novaAuthToast';
    toast.style.cssText = [
      'position:fixed', 'bottom:80px', 'left:50%', 'transform:translateX(-50%)',
      'background:#1a1a1a', 'border:1.5px solid rgba(212,175,55,0.5)',
      'color:#e8e8e8', 'padding:12px 20px', 'border-radius:12px',
      'font-size:13px', 'z-index:9999', 'max-width:300px', 'text-align:center',
      'box-shadow:0 4px 20px rgba(0,0,0,0.6)', 'line-height:1.5'
    ].join(';');
    document.body.appendChild(toast);
  }
  toast.textContent = '🔒 ' + msg;
  toast.style.display = 'block';
  toast.style.opacity = '1';
  setTimeout(() => {
    toast.style.transition = 'opacity 0.4s';
    toast.style.opacity = '0';
    setTimeout(() => { toast.style.display = 'none'; }, 400);
  }, 3000);
}

async function fetchReceiverCoinFromCode() {
  const code = normalizeCode($('manualReceiverCodeName').value);
  if (!code) return;
  if (state.me && code === normalizeCode(state.me.code_name)) {
    setMsg($('payMsg'), 'You cannot pay yourself.');
    $('manualReceiverCoin').value = '';
    state.receiver = null;
    return;
  }
  const res = await api(`/api/account/search?code_name=${encodeURIComponent(code)}`);
  $('manualReceiverCoin').value = res.coin;
  state.receiver = { receiver_code_name: res.code_name, receiver_coin: res.coin, timestamp: null };
  showReceiverBox();
}

async function submitPay() {
  try {
    $('payMsg').textContent = '';
    $('btnPayNow').disabled = true;
    const amount = $('payAmount').value;
    const payerPassword = $('payPassword').value;
    if (!amount || Number(amount) <= 0) throw new Error('Enter valid amount');
    if (!payerPassword) throw new Error('Password is required');
    if (!state.receiver) throw new Error('Receiver info missing');
    // Must be logged in
    if (!state.me) throw new Error('You must be logged in to make a payment.');
    // Must have sufficient balance
    const amountNum = Number(amount);
    if (state.me.coinvalue <= 0) throw new Error('Insufficient balance. Your coin balance is 0.');
    if (state.me.coinvalue < amountNum) throw new Error(`Insufficient balance. You have ${state.me.coinvalue} coin(s), need ${amountNum}.`);
    if (normalizeCode(state.receiver.receiver_code_name) === normalizeCode(state.me.code_name)) {
      throw new Error('You cannot pay yourself.');
    }
    const body = {
      amount: Number(amount),
      payer_password: payerPassword,
      receiver_code_name: state.receiver.receiver_code_name,
      receiver_coin: state.receiver.receiver_coin,
    };
    const res = await api('/api/transaction/pay', 'POST', body);
    const payerRupees = (res.payer_balance * 10000).toLocaleString('en-IN');
    const receiverRupees = (res.receiver_balance * 10000).toLocaleString('en-IN');
    $('successText').textContent = `Your balance: ${res.payer_balance} coins (₹${payerRupees}) | Receiver: ${res.receiver_balance} coins (₹${receiverRupees})`;
    showScreen('paySuccess');
  } catch (e) {
    $('payMsg').textContent = e.message || String(e);
    $('payMsg').style.color = 'var(--danger)';
  } finally {
    $('btnPayNow').disabled = false;
  }
}

// ── SHOW TAB ──────────────────────────────────────────────────────────────

function showTab(tab) {
  const r = $('card-register');
  const l = $('card-login');
  if (r) r.style.display = tab === 'register' ? 'block' : 'none';
  if (l) l.style.display = tab === 'login' ? 'block' : 'none';
  document.querySelectorAll('.auth-tab').forEach((btn, i) => {
    btn.classList.toggle('active', (i === 0) === (tab === 'register'));
  });
  // Reset to step 1 whenever register tab is opened fresh
  if (tab === 'register') regGoPanel(1);
}
window.showTab = showTab;

// ── OTP STATE ─────────────────────────────────────────────────────────────
const otpState = { emailVerified: false };

// ── GOOGLE AUTH CALLBACK (called by GSI library) ──────────────────────────
window.handleGoogleCredential = async function(response) {
  const msgEl = $('authLoginMsg');
  try {
    setMsg(msgEl, 'Signing in with Google...', 'muted');
    const res = await api('/api/google-login', 'POST', { credential: response.credential });
    if (res.needs_setup) {
      // New Google user — pre-fill name & email, go to Step 1 (they must set Code Name first)
      showTab('register');
      if ($('regName'))  $('regName').value  = res.name  || '';
      if ($('regEmail')) $('regEmail').value = res.email || '';
      otpState.emailVerified = true;
      _showEmailVerified();
      $('emailSendRow').style.display = 'none';
      $('emailOtpRow').style.display  = 'none';
      regGoPanel(1);
      setMsg($('regStep1Msg'), 'Google account linked. Please enter your Code Name to continue.');
      return;
    }
    // Existing user — log straight in
    $('loginStatus').textContent = 'Google login successful';
    state.me = await api('/api/me');
    const _handledGoogle = await _afterLoginRedirect(state.me);
    if (!_handledGoogle) { showScreen('dashboard'); await refreshDashboard(true); }
  } catch (e) {
    setMsg(msgEl, e.message || 'Google login failed');
  }
};

function _showEmailVerified() {
  otpState.emailVerified = true;
  const badge = $('emailVerifiedBadge');
  if (badge) { badge.style.display = 'inline-flex'; }
  const wrap = $('verifiedBadges');
  if (wrap) { wrap.style.display = 'flex'; }
  const row = $('emailOtpRow');
  if (row) row.style.display = 'none';
  const sendRow = $('emailSendRow');
  if (sendRow) sendRow.innerHTML = '<div class="verified-line">Email address verified</div>';
}

// ── BIND EVENTS ───────────────────────────────────────────────────────────

// ── REGISTER 3-STEP WIZARD ────────────────────────────────────────────────

function regGoPanel(n) {
  [1,2,3].forEach(i => {
    const panel = $('reg-panel-' + i);
    if (panel) panel.classList.toggle('hidden', i !== n);
    const dot = $('rdot-' + i);
    if (dot) {
      dot.classList.toggle('active', i === n);
      dot.classList.toggle('done',   i < n);
    }
  });
  // Colour the connecting bars
  [1,2].forEach(i => {
    const bar = $('rbar-' + i);
    if (bar) bar.classList.toggle('done', i < n);
  });
}

function regStep1Next() {
  const name     = ($('regName').value || '').trim();
  const codeName = ($('regCodeName').value || '').trim().toUpperCase();
  const msgEl    = $('regStep1Msg');
  if (!name)                                       { setMsg(msgEl, 'Full name is required'); return; }
  if (codeName.length < 5 || codeName.length > 7) { setMsg(msgEl, 'Code name must be 5–7 characters'); return; }
  setMsg(msgEl, '');
  // If email already verified (Google user), keep OTP row hidden on step 2
  if (otpState.emailVerified) {
    $('emailSendRow').style.display = 'none';
    $('emailOtpRow').style.display  = 'none';
  }
  regGoPanel(2);
}

function regStep2Next() {
  const email = ($('regEmail').value || '').trim();
  const msgEl = $('regStep2Msg');
  if (!email || !email.includes('@'))     { setMsg(msgEl, 'Enter a valid email address'); return; }
  if (!otpState.emailVerified)            { setMsg(msgEl, 'Please verify your email before continuing'); return; }
  setMsg(msgEl, '');
  regGoPanel(3);
}

window.regGoPanel    = regGoPanel;
window.regStep1Next  = regStep1Next;
window.regStep2Next  = regStep2Next;

// ── BIND EVENTS ───────────────────────────────────────────────────────────

function bindEvents() {
  $('regCodeName').addEventListener('input', () => setMsg($('regStep1Msg'), ''));
  $('loginCodeName').addEventListener('input', () => setMsg($('authLoginMsg'), ''));

  // ── SEND EMAIL OTP ──
  $('btnSendEmailOtp').addEventListener('click', async () => {
    const email = ($('regEmail').value || '').trim();
    if (!email || !email.includes('@')) { setMsg($('authRegisterMsg'), 'Enter a valid email first'); return; }
    const btn = $('btnSendEmailOtp');
    btn.disabled = true;
    btn.textContent = 'Sending...';
    try {
      await api('/api/otp/send-email', 'POST', { email });
      $('emailOtpRow').style.display = 'block';
      setMsg($('authRegisterMsg'), 'OTP sent to ' + email);
      btn.textContent = 'Resend Email OTP';
    } catch (e) {
      setMsg($('authRegisterMsg'), e.message || 'Failed to send email OTP');
      btn.textContent = 'Send Email OTP';
    } finally {
      btn.disabled = false;
    }
  });

  // ── VERIFY EMAIL OTP ──
  $('btnVerifyEmailOtp').addEventListener('click', async () => {
    const email = ($('regEmail').value || '').trim();
    const otp   = ($('regEmailOtp').value || '').trim();
    if (!otp) { $('emailOtpStatus').textContent = 'Enter the OTP'; return; }
    try {
      await api('/api/otp/verify-email', 'POST', { email, otp });
      _showEmailVerified();
    } catch (e) {
      $('emailOtpStatus').textContent = e.message || 'Invalid OTP';
    }
  });

  // ── SEND PHONE OTP ──
  // Phone OTP removed (Twilio costs money). Phone is stored as plain field.

  // ── REGISTER ──
  $('btnRegister').addEventListener('click', async () => {
    state.authCheckVersion++;
    try {
      $('btnRegister').disabled = true;
      $('authRegisterMsg').textContent = '';

      const name     = ($('regName').value || '').trim();
      const email    = ($('regEmail').value || '').trim();
      const phone    = ($('regPhone').value || '').trim();
      const password = ($('regPassword').value || '').trim();
      const confirm  = ($('regPasswordConfirm').value || '').trim();
      const payload  = {
        name,
        email,
        phone,
        user_id:   normalizeCode($('regUserId').value),
        code_name: normalizeCode($('regCodeName').value),
        password,
      };

      if (!name)                                          throw new Error('Full name is required');
      const codeLen = payload.code_name.length;
      if (codeLen < 5 || codeLen > 7)                    throw new Error('Code name must be 5-7 characters');
      if (!email || !email.includes('@'))                 throw new Error('Valid email is required');
      if (!otpState.emailVerified)                        throw new Error('Please verify your email first');
      if (!password || password.length < 12 || password.length > 16)
                                                          throw new Error('Password must be 12-16 characters');
      if (password !== confirm)                           throw new Error('Passwords do not match');

      const res = await api('/api/register', 'POST', payload);
      await api('/api/login', 'POST', { code_name: res.code_name, password });
      $('loginStatus').textContent = 'Account created';
      state.me = await api('/api/me');
      const _handledReg = await _afterLoginRedirect(state.me);
      if (!_handledReg) { showScreen('dashboard'); await refreshDashboard(true); }
    } catch (e) {
      setMsg($('authRegisterMsg'), e.message || String(e));
      showScreen('auth');
    } finally {
      $('btnRegister').disabled = false;
    }
  });

  // ── LOGIN ──
  $('btnLogin').addEventListener('click', async () => {
    state.authCheckVersion++;
    try {
      $('btnLogin').disabled = true;
      $('authLoginMsg').textContent = '';
      const payload = {
        code_name: normalizeCode($('loginCodeName').value),
        password:  ($('loginPassword').value || '').trim(),
      };
      if (!payload.code_name) throw new Error('Code name required');
      if (!payload.password)  throw new Error('Password required');
      await api('/api/login', 'POST', payload);
      $('loginStatus').textContent = 'Login successful';
      state.me = await api('/api/me');
      const _handledLogin = await _afterLoginRedirect(state.me);
      if (!_handledLogin) { showScreen('dashboard'); await refreshDashboard(true); }
    } catch (e) {
      setMsg($('authLoginMsg'), e.message || String(e));
      showScreen('auth');
    } finally {
      $('btnLogin').disabled = false;
    }
  });

  // ── NAV DRAWER ──────────────────────────────────────────────────────────
  function openNavDrawer() {
    $('navDrawer').classList.add('open');
    $('navDrawerOverlay').classList.add('open');
    $('btnNavToggle').classList.add('open');
  }
  function closeNavDrawer() {
    $('navDrawer').classList.remove('open');
    $('navDrawerOverlay').classList.remove('open');
    $('btnNavToggle').classList.remove('open');
  }
  $('btnNavToggle')?.addEventListener('click', () => {
    $('navDrawer').classList.contains('open') ? closeNavDrawer() : openNavDrawer();
  });
  $('navDrawerOverlay')?.addEventListener('click', closeNavDrawer);

  $('drawerBtnHome')?.addEventListener('click',    () => { closeNavDrawer(); openHomeScreen(); });
  $('drawerBtnConnect')?.addEventListener('click', () => { closeNavDrawer(); openConnectScreen(); });
  $('drawerBtnProfile')?.addEventListener('click', () => { closeNavDrawer(); openProfileScreen(); });
  $('drawerBtnStore')?.addEventListener('click',   () => { closeNavDrawer(); openStore(); });
  $('drawerBtnAbout')?.addEventListener('click',   () => { closeNavDrawer(); openAboutModal(); });
  $('drawerBtnLogout')?.addEventListener('click', async () => {
    closeNavDrawer();
    state.authCheckVersion++;
    try { await api('/api/logout', 'POST'); } catch {}
    try { localStorage.removeItem('nova_screen'); } catch {}
    state.me = null; state.receiver = null;
    showScreen('auth');
    $('loginStatus').textContent = '';
  });
  // ────────────────────────────────────────────────────────────────────────

  $('btnAbout')?.addEventListener('click', () => openAboutModal());
  $('btnCloseAbout').addEventListener('click', () => closeAboutModal());

  const btnCloseHistory = $('btnCloseHistory');
  if (btnCloseHistory) btnCloseHistory.addEventListener('click', () => closeHistoryModal());

  // Invoice buttons
  const btnCloseInvoice = $('btnCloseInvoice');
  if (btnCloseInvoice) btnCloseInvoice.addEventListener('click', closeInvoiceModal);
  const btnCloseInvoice2 = $('btnCloseInvoice2');
  if (btnCloseInvoice2) btnCloseInvoice2.addEventListener('click', closeInvoiceModal);
  const btnPrintInvoice = $('btnPrintInvoice');
  if (btnPrintInvoice) btnPrintInvoice.addEventListener('click', () => window.print());

  $('btnSearch').addEventListener('click', async () => {
    const code = normalizeCode($('searchCodeName').value);
    const box = $('searchResult');
    box.classList.add('hidden');
    box.innerHTML = '';
    try {
      if (!code) throw new Error('Enter code name');
      const res = await api(`/api/account/search?code_name=${encodeURIComponent(code)}`);
      box.classList.remove('hidden');
      box.innerHTML = `
        <div class="muted">Account found</div>
        <div class="receiver-line" style="margin-top:8px;">User name: ${res.code_name}</div>
        <div class="receiver-line">Account no (coin): ${res.coin}</div>
        <div class="receiver-line">Balance: ${res.coinvalue} Coin${res.coinvalue !== 1 ? 's' : ''} <span class="muted small">(₹${(res.coinvalue * 10000).toLocaleString('en-IN')})</span></div>
      `;
    } catch (e) {
      box.classList.remove('hidden');
      box.innerHTML = `<div style="color:var(--danger);font-weight:900;margin-top:6px;">Error: ${e.message || e}</div>`;
    }
  });

  $('btnRefreshTx').addEventListener('click', async () => {
    try { await refreshDashboard(); } catch {}
  });

  $('btnOpenScanner').addEventListener('click', () => openScannerModal());
  $('btnCloseScanner').addEventListener('click', () => closeScannerModal());

  $('btnVerifyPastedQr').addEventListener('click', async () => {
    const qrText = ($('qrPaste').value || '').trim();
    if (!qrText) {
      $('scannerMsg').textContent = 'Paste QR text first.';
      $('scannerMsg').style.color = 'var(--danger)';
      return;
    }
    await verifyQrTextAndGoPay(qrText);
  });

  $('btnManualPay').addEventListener('click', () => showPayScreenWithManual());
  $('btnBackToDashFromPay').addEventListener('click', async () => { showScreen('dashboard'); await refreshDashboard(); });
  $('btnBackToDashFromSuccess').addEventListener('click', async () => { showScreen('dashboard'); await refreshDashboard(); });

  $('manualReceiverCodeName').addEventListener('change', async () => {
    try { await fetchReceiverCoinFromCode(); }
    catch (e) { setMsg($('payMsg'), e.message || String(e)); }
  });

  $('btnPayNow').addEventListener('click', submitPay);
  $('btnStartRun').addEventListener('click', startRun);
  $('btnStopRun').addEventListener('click', stopRun);

  $('scannerModal').addEventListener('click', (e) => { if (e.target === $('scannerModal')) closeScannerModal(); });
  const aboutModal = $('aboutModal');
  if (aboutModal) aboutModal.addEventListener('click', (e) => { if (e.target === aboutModal) closeAboutModal(); });
}


// ── RUN FEATURE ────────────────────────────────────────────────────────────

function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function getISTMinutes() {
  const now = new Date();
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60000;
  const istMs = utcMs + (5 * 60 + 30) * 60000;
  const d = new Date(istMs);
  return d.getHours() * 60 + d.getMinutes();
}

function updateRunUI() {
  const r = state.run;
  $('runDistance').textContent = r.totalKm.toFixed(2) + ' km';
  $('runSpeed').textContent = r.currentSpeed.toFixed(1) + ' km/h';
  $('runPoints').textContent = r.points.length;
  const remaining = Math.max(0, 10 - r.totalKm);
  $('runStatus').textContent = r.totalKm >= 10
    ? '10 km done! Stop to claim 1 coin.'
    : remaining.toFixed(2) + ' km more needed (no carry-forward)';
}

function startRun() {
  if (!navigator.geolocation) {
    $('runMsg').textContent = 'GPS not supported on this device.';
    $('runMsg').style.color = 'var(--danger)';
    return;
  }
  const istMinutes = getISTMinutes();
  const before5am = istMinutes < 5 * 60;
  const after8am = istMinutes >= 8 * 60;
  if (after8am) {
    $('runMsg').textContent = 'Running window closed (past 8:00 AM IST). Come back before 5:00 AM tomorrow.';
    $('runMsg').style.color = 'var(--danger)';
    return;
  }
  state.run = { active: true, watchId: null, points: [], lastPos: null, totalKm: 0, currentSpeed: 0 };
  $('btnStartRun').classList.add('hidden');
  $('btnStopRun').classList.remove('hidden');
  $('runStats').classList.remove('hidden');
  if (before5am) {
    $('runMsg').textContent = 'GPS tracking started. Run window: 5:00 AM - 8:00 AM IST.';
    $('runMsg').style.color = 'var(--ok)';
  } else {
    $('runMsg').textContent = 'Running (5-8 AM IST window). Server will validate timing.';
    $('runMsg').style.color = 'var(--gold2)';
  }
  updateRunUI();
  state.run.watchId = navigator.geolocation.watchPosition(
    (pos) => {
      if (!state.run.active) return;
      const { latitude: lat, longitude: lon, accuracy } = pos.coords;
      const timestamp = pos.timestamp / 1000;
      const curMin = getISTMinutes();
      if (curMin >= 8 * 60) { stopRun(); $('runMsg').textContent = 'Run window ended at 8:00 AM IST. Submitting...'; return; }
      const point = { lat, lon, timestamp, accuracy_m: accuracy };
      if (state.run.lastPos) {
        const segKm = haversineKm(state.run.lastPos.lat, state.run.lastPos.lon, lat, lon);
        const dtHours = (timestamp - state.run.lastPos.timestamp) / 3600;
        const spd = dtHours > 0 ? segKm / dtHours : 0;
        state.run.currentSpeed = spd;
        if (spd >= 3 && spd <= 20) state.run.totalKm += segKm;
      }
      state.run.points.push(point);
      state.run.lastPos = { lat, lon, timestamp };
      updateRunUI();
    },
    (err) => {
      $('runMsg').textContent = 'GPS error: ' + err.message + '. Allow location access.';
      $('runMsg').style.color = 'var(--danger)';
    },
    { enableHighAccuracy: true, maximumAge: 3000, timeout: 15000 }
  );
}

function stopRun() {
  if (state.run.watchId !== null) { navigator.geolocation.clearWatch(state.run.watchId); state.run.watchId = null; }
  state.run.active = false;
  $('btnStopRun').classList.add('hidden');
  $('btnStartRun').classList.remove('hidden');
  const points = state.run.points;
  if (points.length < 2) {
    $('runMsg').textContent = 'Not enough GPS data recorded.';
    $('runMsg').style.color = 'var(--danger)';
    return;
  }
  $('runMsg').textContent = 'Submitting run to server...';
  $('runMsg').style.color = 'var(--muted)';
  api('/api/run/earn', 'POST', { gps_points: points })
    .then((res) => {
      const earned = res.coins_earned || 0;
      const rupeeVal = (earned * 10000).toLocaleString('en-IN');
      $('runMsg').textContent = res.message || `Earned ${earned} coin(s) worth ₹${rupeeVal}!`;
      $('runMsg').style.color = earned > 0 ? 'var(--ok)' : 'var(--gold2)';
      $('runStats').classList.add('hidden');
      refreshDashboard().catch(() => {});
    })
    .catch((e) => {
      $('runMsg').textContent = 'Run rejected: ' + (e.message || e);
      $('runMsg').style.color = 'var(--danger)';
    });
}

// ── BOOT ──────────────────────────────────────────────────────────────────

// Guard: ensure Google accounts.id.initialize() is called only once
let _googleInitDone = false;

async function boot() {
  screens = {
    auth:       $('screen-auth'),
    dashboard:  $('screen-dashboard'),
    pay:        $('screen-pay'),
    paySuccess: $('screen-paySuccess'),
    home:       $('screen-home'),
    profile:    $('screen-profile'),
    connect:    $('screen-connect'),
  };
  enforceUppercaseInputs();
  bindEvents();
  initSplash();
  initBuyCoins();
  showSplash(true);
  // Show auth initially hidden, splash shows
  Object.values(screens).forEach(el => el && el.classList.add('hidden'));

  // Run config fetch and login check in parallel — don't await config before checking login
  const configPromise = !_googleInitDone ? api('/api/config', 'GET').then(cfg => {
    if (cfg && cfg.google_client_id) {
      const onloadDiv = document.getElementById('g_id_onload');
      if (onloadDiv) onloadDiv.setAttribute('data-client_id', cfg.google_client_id);
      if (window.google && window.google.accounts && window.google.accounts.id) {
        window.google.accounts.id.initialize({
          client_id: cfg.google_client_id,
          callback: window.handleGoogleCredential,
          auto_select: false,
        });
        _googleInitDone = true;
        const btnContainer = document.querySelector('.g_id_signin');
        if (btnContainer) {
          window.google.accounts.id.renderButton(btnContainer, {
            type: 'standard', size: 'large', theme: 'filled_black',
            text: 'sign_in_with', shape: 'rectangular', logo_alignment: 'left',
            width: btnContainer.offsetWidth || 300,
          });
        }
      }
    }
  }).catch(e => console.warn('Config load failed:', e)) : Promise.resolve();

  // Both run simultaneously
  await Promise.all([configPromise, checkLoginOnLoad()]);
}

// ── PARTICLE ANIMATION ─────────────────────────────────────────────────────

function bootParticles() {
  const canvas = $('authParticles');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let W, H, particles;

  function resize() { W = canvas.width = canvas.offsetWidth; H = canvas.height = canvas.offsetHeight; }
  function mkParticle() {
    return { x: Math.random() * W, y: Math.random() * H, r: Math.random() * 1.5 + 0.4,
      vx: (Math.random() - 0.5) * 0.4, vy: -(Math.random() * 0.5 + 0.2),
      life: Math.random(), maxLife: Math.random() * 0.5 + 0.5 };
  }
  function init() { resize(); particles = Array.from({ length: 55 }, mkParticle); }
  function draw() {
    ctx.clearRect(0, 0, W, H);
    particles.forEach((p, i) => {
      p.x += p.vx; p.y += p.vy; p.life += 0.004;
      if (p.life > p.maxLife || p.y < 0) particles[i] = mkParticle();
      const alpha = Math.sin((p.life / p.maxLife) * Math.PI) * 0.6;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(212,175,55,${alpha})`;
      ctx.fill();
    });
    requestAnimationFrame(draw);
  }
  init(); draw();
  window.addEventListener('resize', () => { resize(); });
}

window.addEventListener('DOMContentLoaded', () => { boot(); bootParticles(); });

// ── Android Keyboard Fix ──────────────────────────────────────────────────────
// Problem: When Android soft keyboard opens, the browser shrinks window.innerHeight
// but position:fixed elements still reference the FULL original viewport.
// Result: chatInputBar gets hidden behind the keyboard, and scrolling breaks.
//
// Fix: Use visualViewport API to track the VISIBLE area precisely.
// On every resize/scroll of the visual viewport, we:
//   1. Set chatView height = visualViewport.height (visible area only)
//   2. Set chatView top    = visualViewport.offsetTop (accounts for scroll offset)
//   3. Scroll messages to bottom so latest message is always visible
//
// This makes the chat behave exactly like WhatsApp/Instagram on Android:
//   - Input bar always sits just above the keyboard
//   - Messages list fills only the visible space above the input bar
//   - No content hidden behind the keyboard ever
// ──────────────────────────────────────────────────────────────────────────────

function _applyChatViewport() {
  const chatView = $('chatView');
  if (!chatView || chatView.style.display === 'none') return;
  const vv = window.visualViewport;
  if (vv) {
    chatView.style.top    = vv.offsetTop + 'px';
    chatView.style.height = vv.height + 'px';
  } else {
    chatView.style.top    = '0px';
    chatView.style.height = window.innerHeight + 'px';
  }
  // Always scroll messages to bottom after resize so last message is visible
  const msgList = $('chatMessages');
  if (msgList) msgList.scrollTop = msgList.scrollHeight;
}

if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', _applyChatViewport);
  window.visualViewport.addEventListener('scroll', _applyChatViewport);
} else {
  // Fallback for older Android browsers without visualViewport
  window.addEventListener('resize', _applyChatViewport);
}

// ═══════════════════════════════════════════════════════════════════════════════
// NOVA WATCH STORE — STORE LOGIC
// All original Nova code is untouched above.
// ═══════════════════════════════════════════════════════════════════════════════

const STORE_ADMIN = 'SHADOW'; // Must match STORE_ADMIN_CODE_NAME in app.py
const RZP_KEY     = 'rzp_test_SiOpSGdjiLhfLU';

let storeData = { products: [], settings: {}, filtered: [] };

// ── Helpers ───────────────────────────────────────────────────────────────────

function storeApi(path, method = 'GET', body) {
  return api(path, method, body);
}

function isStoreAdmin() {
  return state.me && state.me.code_name &&
         state.me.code_name.toUpperCase() === STORE_ADMIN.toUpperCase();
}

function showStoreMsg(elId, msg, kind = 'error') {
  const el = $(elId);
  if (!el) return;
  el.textContent = msg;
  el.style.color = kind === 'ok' ? 'var(--ok)' : 'var(--danger)';
  el.style.display = 'block';
}

// ── Navigate to store ─────────────────────────────────────────────────────────

async function openStore() {
  showScreen('store');
  $('storeMain').style.display = 'block';
  $('storeDetail').style.display = 'none';
  // Show admin button if admin
  const adminBtn = $('btnAdminPanel');
  if (adminBtn) adminBtn.style.display = isStoreAdmin() ? 'block' : 'none';
  await loadStoreProducts();
}

async function loadStoreProducts() {
  try {
    const [res, catRes] = await Promise.all([
      storeApi('/api/store/products'),
      api('/api/categories').catch(() => ({ categories: [] })),
    ]);
    storeData.products = res.products || [];
    storeData.settings = res.settings || {};
    storeData.filtered = storeData.products;
    const rate = parseInt(storeData.settings.coin_to_inr || 10000);
    const rateEl = $('storeCoinRate');
    if (rateEl) rateEl.textContent = rate.toLocaleString('en-IN');
    // Rebuild category filter buttons dynamically
    const cats = catRes.categories || [];
    const btnsEl = $('storeCatBtns');
    if (btnsEl && cats.length) {
      btnsEl.innerHTML = `<button class="btn btn-ghost btn-sm store-cat-btn active" data-cat="all">All</button>`
        + cats.map(c => `<button class="btn btn-ghost btn-sm store-cat-btn" data-cat="${c.slug}">${c.name}</button>`).join('');
    }
    renderStoreGrid(storeData.filtered);
  } catch (e) {
    $('storeProductGrid').innerHTML = `<div class="muted small" style="padding:20px 0;">Failed to load products: ${e.message||e}</div>`;
  }
}

function renderStoreGrid(products) {
  const grid = $('storeProductGrid');
  if (!products.length) {
    grid.innerHTML = '<div class="muted small" style="padding:20px 0;text-align:center;">No products yet. Check back soon.</div>';
    return;
  }
  const html = `<div class="store-grid">${products.map(p => {
    const sc = p.stock === 0 ? 'out' : p.stock < 5 ? 'low' : '';
    const st = p.stock === 0 ? 'Out of Stock' : p.stock < 5 ? `Only ${p.stock} left` : 'In Stock';
    const imgHtml = p.image_url
      ? `<div class="store-card-img"><img src="${p.image_url}" alt="${p.name}" onerror="this.parentElement.innerHTML='⌚'"/></div>`
      : `<div class="store-card-img">⌚</div>`;
    const coinsOk = p.coins_enabled !== false && storeData.settings.coins_enabled !== '0';
    const inrOk   = p.inr_enabled   !== false && storeData.settings.razorpay_enabled !== '0';
    const payBadges = [
      coinsOk ? `<span style="font-size:10px;background:rgba(212,175,55,0.15);color:var(--gold2);border-radius:4px;padding:2px 5px;">◎ Coins</span>` : '',
      inrOk   ? `<span style="font-size:10px;background:rgba(255,255,255,0.08);color:#ccc;border-radius:4px;padding:2px 5px;">₹ INR</span>` : '',
    ].filter(Boolean).join('');
    return `<div class="store-card" onclick="openProductDetail(${p.id})">
      ${imgHtml}
      <div class="store-card-body">
        <div class="store-card-cat">${p.category}</div>
        <div class="store-card-name">${p.name}</div>
        ${coinsOk ? `<div class="store-card-price-coin">◎ ${p.price_coins}</div>` : ''}
        ${inrOk   ? `<div class="store-card-price-inr">₹${Number(p.price_inr).toLocaleString('en-IN')}</div>` : ''}
        ${payBadges ? `<div style="display:flex;gap:4px;flex-wrap:wrap;margin:4px 0 2px;">${payBadges}</div>` : ''}
        <div class="store-card-stock ${sc}">${st}</div>
      </div>
    </div>`;
  }).join('')}</div>`;
  grid.innerHTML = html;
}

function filterStoreProducts(cat) {
  storeData.filtered = cat === 'all'
    ? storeData.products
    : storeData.products.filter(p => p.category === cat);
  renderStoreGrid(storeData.filtered);
}

// ── Product detail ────────────────────────────────────────────────────────────

function openProductDetail(id) {
  const p = storeData.products.find(x => x.id === id);
  if (!p) return;
  $('storeMain').style.display = 'none';
  $('storeDetail').style.display = 'block';

  const svc_c = parseFloat(storeData.settings.service_charge_coins || 0.05);
  const svc_i = parseInt(storeData.settings.service_charge_inr || 500);
  const total_c = (p.price_coins + svc_c).toFixed(4);
  const total_i = p.price_inr + svc_i;
  // Global settings AND per-product toggles both must be enabled
  const ce = storeData.settings.coins_enabled !== '0' && p.coins_enabled !== false;
  const re = storeData.settings.razorpay_enabled !== '0' && p.inr_enabled !== false;
  const oos = p.stock === 0;
  const sc = p.stock === 0 ? 'out' : p.stock < 5 ? 'low' : '';
  const st = p.stock === 0 ? 'Out of Stock' : p.stock < 5 ? `Only ${p.stock} left` : `${p.stock} in stock`;

  const feats = p.features
    ? p.features.split('\n').filter(f => f.trim())
        .map(f => `<div class="store-detail-feat-item">${f.trim()}</div>`).join('')
    : '';

  const imgHtml = p.image_url
    ? `<div class="store-detail-img" style="cursor:zoom-in;" onclick="openLightbox('${p.image_url}')"><img src="${p.image_url}" alt="${p.name}" onerror="this.parentElement.innerHTML='⌚'"/><div style="position:absolute;bottom:8px;right:10px;background:rgba(0,0,0,0.5);color:#fff;font-size:11px;padding:3px 7px;border-radius:4px;pointer-events:none;">Tap to zoom</div></div>`
    : `<div class="store-detail-img">⌚</div>`;

  const balLine = state.me ? `<div class="muted small">Your balance: ◎${state.me.coinvalue}</div>` : '';

  $('storeDetailContent').innerHTML = `
    ${imgHtml}
    <div class="store-detail-cat">${p.category}</div>
    <div class="store-detail-name">${p.name}</div>
    <div class="store-detail-desc">${p.description || 'A precision timepiece from the Nova collection.'}</div>
    ${feats ? `<div class="store-detail-features"><div class="muted small" style="margin-bottom:6px;letter-spacing:.1em;text-transform:uppercase;">Specifications</div>${feats}</div>` : ''}
    <div class="store-price-box">
      <div class="store-price-row"><span class="store-price-label">Nova Coins</span><span class="store-price-val gold">◎ ${p.price_coins}</span></div>
      <div class="store-price-row"><span class="store-price-label">Razorpay (INR)</span><span class="store-price-val">₹${Number(p.price_inr).toLocaleString('en-IN')}</span></div>
      <div class="muted small" style="margin-top:6px;">+ Service: ◎${svc_c} / ₹${svc_i.toLocaleString('en-IN')}</div>
    </div>
    <div class="store-card-stock ${sc}" style="margin-bottom:12px;font-size:12px;">${st}</div>
    ${oos ? `<div class="muted small" style="text-align:center;padding:16px 0;">This product is currently out of stock.</div>` : `
      ${!state.me ? `<button class="btn btn-gold btn-full" onclick="showTab('login');showScreen('auth');">Login to Buy</button>` : `
        <div class="buy-method-tabs">
          ${ce ? `<button class="buy-method-tab active" data-method="coin" onclick="switchBuyMethod('coin',this)">◎ Coins</button>` : ''}
          ${re ? `<button class="buy-method-tab ${!ce?'active':''}" data-method="rz" onclick="switchBuyMethod('rz',this)"><svg width="14" height="14" style="width:14px; height:14px; color:white;" role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>Razorpay</title><path d="M22.436 0l-11.91 7.773-1.174 4.276 6.625-4.297L11.65 24h4.391l6.395-24zM14.26 10.098L3.389 17.166 1.564 24h9.008l3.688-13.902Z" fill="white"></path></svg>Razorpay (INR)</button>` : ''}        </div>
        ${ce ? `<div class="buy-method-panel active" id="buy-panel-coin">
          <div style="background:rgba(212,175,55,0.07);border:1px solid rgba(212,175,55,0.15);border-radius:6px;padding:10px;margin-bottom:10px;font-size:13px;">
            Total: <strong style="color:var(--gold2)">◎${total_c}</strong> (incl. ◎${svc_c} service)
            ${balLine}
          </div>
          <div class="ship-title">Delivery Information</div>
          <div class="field"><input id="coinShipName" class="field-input" placeholder="Full Name *" required/></div>
          <div class="field"><input id="coinShipPhone" class="field-input" placeholder="Phone Number *" inputmode="numeric" maxlength="10" required/></div>
          <div class="field">
            <div style="display:flex;gap:6px;margin-bottom:6px;">
              <button type="button" class="btn btn-ghost btn-sm" onclick="openMapPicker('coinShipAddr')" style="font-size:11px;">Choose on Map</button>
              <span class="muted small" style="align-self:center;">or type below</span>
            </div>
            <textarea id="coinShipAddr" rows="2" class="field-input" placeholder="Delivery Address *" required></textarea>
          </div>
          <div class="field"><label style="font-size:12px;color:var(--muted);">Your Password (to confirm)</label><div class="pass-wrap"><input id="coinBuyPass" type="password" class="field-input" placeholder="Enter password"/><button type="button" class="eye-btn" onclick="togglePass('coinBuyPass',this)" tabindex="-1" aria-label="Show/hide password"><svg class="eye-icon eye-closed" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg><svg class="eye-icon eye-open" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:none"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button></div></div>
          <button class="btn btn-gold btn-full" style="margin-top:10px;" onclick="buyWithCoins(${p.id},${total_c})">Confirm — ◎${total_c}</button>
          <div id="coinBuyMsg" class="msg"></div>
        </div>` : ''}
        ${re ? `<div class="buy-method-panel ${!ce?'active':''}" id="buy-panel-rz">
          <div style="background:rgba(212,175,55,0.07);border:1px solid rgba(212,175,55,0.15);border-radius:6px;padding:10px;margin-bottom:10px;font-size:13px;">
            Total: <strong>₹${Number(total_i).toLocaleString('en-IN')}</strong> (incl. ₹${svc_i.toLocaleString('en-IN')} service)
          </div>
          <div class="ship-title">Delivery Information</div>
          <div class="field"><input id="rzShipName" class="field-input" placeholder="Full Name *" required/></div>
          <div class="field"><input id="rzShipPhone" class="field-input" placeholder="Phone Number *" inputmode="numeric" maxlength="10" required/></div>
          <div class="field">
            <div style="display:flex;gap:6px;margin-bottom:6px;">
              <button type="button" class="btn btn-ghost btn-sm" onclick="openMapPicker('rzShipAddr')" style="font-size:11px;">Choose on Map</button>
              <span class="muted small" style="align-self:center;">or type below</span>
            </div>
            <textarea id="rzShipAddr" rows="2" class="field-input" placeholder="Delivery Address *" required></textarea>
          </div>
          <button class="btn btn-gold btn-full" style="margin-top:10px;" onclick="buyWithRazorpay(${p.id},${total_i})">Pay ₹${Number(total_i).toLocaleString('en-IN')} via Razorpay</button>
          <div id="rzBuyMsg" class="msg"></div>
        </div>` : ''}
      `}
    `}
  `;
}

function switchBuyMethod(method, btn) {
  document.querySelectorAll('.buy-method-tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.buy-method-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  const panel = $('buy-panel-' + method);
  if (panel) panel.classList.add('active');
}

// ── Buy with Coins ────────────────────────────────────────────────────────────

async function buyWithCoins(productId, totalCoins) {
  const pass    = ($('coinBuyPass')   ? $('coinBuyPass').value   : '').trim();
  const name    = ($('coinShipName')  ? $('coinShipName').value  : '').trim();
  const phone   = ($('coinShipPhone') ? $('coinShipPhone').value : '').trim();
  const address = ($('coinShipAddr')  ? $('coinShipAddr').value  : '').trim();
  const msgEl   = $('coinBuyMsg');

  if (!pass)    { setMsg(msgEl, 'Password required');        return; }
  if (!name)    { setMsg(msgEl, 'Full name required');       return; }
  if (!phone)   { setMsg(msgEl, 'Phone number required');    return; }
  if (!address) { setMsg(msgEl, 'Delivery address required'); return; }
  if (!state.me || state.me.coinvalue < totalCoins) {
    setMsg(msgEl, `Insufficient coins. Need ◎${totalCoins}, have ◎${state.me?.coinvalue||0}`); return;
  }

  setMsg(msgEl, 'Processing...', 'ok');
  try {
    const res = await storeApi('/api/store/order/coin', 'POST', {
      product_id: productId, password: pass,
      shipping: { name, phone, address }
    });
    state.me.coinvalue = res.new_balance;
    const _rate = parseInt(storeData.settings.coin_to_inr || 10000);
    const _rupees = (res.new_balance * _rate).toLocaleString('en-IN');
    const _coinDisp = `${res.new_balance} (₹${_rupees})`;
    if ($('meCoinValue'))        $('meCoinValue').textContent        = _coinDisp;
    if ($('sidebarMeCoinValue')) $('sidebarMeCoinValue').textContent = _coinDisp;
    setMsg(msgEl, `✓ Order placed! #${res.order_no} — Invoice will be emailed to you.`, 'ok');
    await loadStoreProducts();
  } catch (e) {
    setMsg(msgEl, e.message || 'Order failed');
  }
}

// ── Buy with Razorpay ─────────────────────────────────────────────────────────

async function buyWithRazorpay(productId, totalInr) {
  const name    = ($('rzShipName')  ? $('rzShipName').value  : '').trim();
  const phone   = ($('rzShipPhone') ? $('rzShipPhone').value : '').trim();
  const address = ($('rzShipAddr')  ? $('rzShipAddr').value  : '').trim();
  const msgEl   = $('rzBuyMsg');

  if (!name)    { setMsg(msgEl, 'Full name required');        return; }
  if (!phone)   { setMsg(msgEl, 'Phone number required');     return; }
  if (!address) { setMsg(msgEl, 'Delivery address required'); return; }

  setMsg(msgEl, 'Creating order...', 'ok');
  try {
    const order = await storeApi('/api/store/order/razorpay/create', 'POST', { product_id: productId });
    const options = {
      key: RZP_KEY,
      amount: order.amount,
      currency: order.currency,
      name: 'Nova Watch Store',
      description: order.product_name,
      order_id: order.razorpay_order_id,
      handler: async (resp) => {
        setMsg(msgEl, 'Verifying payment...', 'ok');
        try {
          const vRes = await storeApi('/api/store/order/razorpay/verify', 'POST', {
            razorpay_order_id: resp.razorpay_order_id,
            razorpay_payment_id: resp.razorpay_payment_id,
            razorpay_signature: resp.razorpay_signature,
            order_no: order.order_no,
            shipping: { name, phone, address }
          });
          setMsg(msgEl, `Payment confirmed! Order #${vRes.order_no} — Invoice will be emailed to you.`, 'ok');
          await loadStoreProducts();
        } catch (e2) {
          setMsg(msgEl, 'Verification failed: ' + (e2.message || e2));
        }
      },
      prefill: { name: state.me?.code_name || '', contact: phone },
      theme: { color: '#d4af37' },
      modal: { ondismiss: () => setMsg(msgEl, 'Payment cancelled.') }
    };
    const rzp = new Razorpay(options);
    rzp.on('payment.failed', (r) => setMsg(msgEl, 'Payment failed: ' + (r.error?.description || 'Unknown')));
    setMsg(msgEl, '');
    rzp.open();
  } catch (e) {
    setMsg(msgEl, 'Could not initiate payment: ' + (e.message || e));
  }
}

// ── My Orders Modal ───────────────────────────────────────────────────────────

async function openMyOrdersModal() {
  const modal = $('myOrdersModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  $('myOrdersList').innerHTML = '<div class="muted small" style="padding:16px 0;">Loading...</div>';
  try {
    const res = await storeApi('/api/store/my-orders');
    const orders = res.orders || [];
    if (!orders.length) {
      $('myOrdersList').innerHTML = '<div class="muted small" style="padding:16px 0;text-align:center;">No orders yet.</div>';
      return;
    }
    $('myOrdersList').innerHTML = orders.map(o => {
      const isCancelled = o.status === 'CANCELLED';
      const coinRefund  = isCancelled && o.payment_method === 'COIN' && o.refund_coins > 0;
      const refundLine  = coinRefund
        ? `<div style="margin-top:6px;padding:7px 10px;background:rgba(72,187,120,0.08);border-left:3px solid var(--ok);border-radius:4px;font-size:12px;color:var(--ok);">
             ✓ <strong>◎${o.refund_coins} refunded</strong> to your wallet${o.refunded_at ? ' on ' + o.refunded_at.slice(0,10) : ''}.
           </div>`
        : (isCancelled && o.payment_method !== 'COIN'
            ? `<div style="margin-top:6px;padding:7px 10px;background:rgba(255,165,0,0.07);border-left:3px solid orange;border-radius:4px;font-size:12px;color:orange;">
                 INR refund will be processed manually within 3–5 business days.
               </div>`
            : '');
      return `
      <div class="my-order-item">
        <div class="my-order-no">#${o.order_no}</div>
        <div class="my-order-name">${o.product_name}</div>
        <div class="my-order-meta">
          ${o.payment_method === 'COIN' ? `◎${o.coins_spent}` : `₹${Number(o.inr_paid/100).toLocaleString('en-IN')}`}
          &nbsp;<span class="status-pill ${o.status}">${o.status}</span>
        </div>
        <div class="my-order-meta">${o.created_at ? o.created_at.slice(0,16) : ''}</div>
        ${refundLine}
        ${o.status === 'DELIVERED' ? `<a class="inv-dl-btn" href="/api/store/invoice/${o.order_no}" target="_blank" download style="color:#d4af37;">⬇ Download Invoice PDF</a>` : ''}
      </div>`;
    }).join('');
  } catch (e) {
    $('myOrdersList').innerHTML = `<div style="color:var(--danger)">Error: ${e.message||e}</div>`;
  }
}

// ── Admin ─────────────────────────────────────────────────────────────────────

function openAdminPanel() {
  if (!isStoreAdmin()) return;
  showScreen('admin');
  // Restore last visited tab (or default to products)
  const savedTab = (function() { try { return localStorage.getItem('nova_admin_tab') || 'products'; } catch { return 'products'; } })();
  switchAdminTab(savedTab);
  // Pre-load background tabs
  if (savedTab !== 'settings')   loadAdminSettings();
  if (savedTab !== 'categories') loadAdminCategories();
}

function switchAdminTab(tab) {
  document.querySelectorAll('.admin-tab-panel').forEach(p => p.style.display = 'none');
  document.querySelectorAll('.admin-tab-btn').forEach(b => {
    b.classList.remove('active');
    b.style.borderBottom = 'none';
  });
  const panel = $('admin-tab-' + tab);
  if (panel) panel.style.display = 'block';
  const btn = document.querySelector(`.admin-tab-btn[data-atab="${tab}"]`);
  if (btn) { btn.classList.add('active'); btn.style.borderBottom = '2px solid var(--gold2)'; }
  try { localStorage.setItem('nova_admin_tab', tab); } catch {}

  if (tab === 'products')   loadAdminProducts();
  if (tab === 'orders')     loadAdminOrders();
  if (tab === 'users')      { loadAdminUsers(); loadCoinLogs(); }
  if (tab === 'broadcast')  loadAdminBroadcasts();
  if (tab === 'categories') loadAdminCategories();
  if (tab === 'settings')   loadAdminSettings();
}

async function loadAdminProducts() {
  const el = $('adminProductList');
  el.innerHTML = '<div class="muted small">Loading...</div>';
  try {
    const res = await storeApi('/api/admin/products');
    const prods = res.products || [];
    if (!prods.length) { el.innerHTML = '<div class="muted small">No products. Click + Add to create one.</div>'; return; }
    el.innerHTML = prods.map(p => `
      <div class="admin-product-row">
        <div class="admin-product-thumb">
          ${p.image_url ? `<img src="${p.image_url}" alt="${p.name}" onerror="this.parentElement.innerHTML='⌚'"/>` : '⌚'}
        </div>
        <div class="admin-product-info">
          <div class="admin-product-name">${p.name}</div>
          <div class="admin-product-meta">◎${p.price_coins} / ₹${Number(p.price_inr).toLocaleString('en-IN')} · Stock: ${p.stock} · ${p.active ? '<span style="color:var(--ok)">Active</span>' : '<span style="color:var(--danger)">Hidden</span>'}</div>
        </div>
        <div class="admin-product-actions">
          <button class="btn btn-ghost btn-sm" onclick='openProductEditModal(${JSON.stringify(p)})'>Edit</button>
          <button class="btn btn-ghost btn-sm" style="color:var(--danger)" onclick="adminDeleteProduct(${p.id},'${p.name.replace(/'/g,"\\'")}')">Del</button>
        </div>
      </div>`).join('');
  } catch (e) {
    el.innerHTML = `<div style="color:var(--danger)">${e.message||e}</div>`;
  }
}

async function loadAdminOrders() {
  const el = $('adminOrderList');
  el.innerHTML = '<div class="muted small">Loading...</div>';
  try {
    const res = await storeApi('/api/admin/orders');
    const orders = res.orders || [];
    if (!orders.length) { el.innerHTML = '<div class="muted small">No orders yet.</div>'; return; }
    el.innerHTML = orders.map(o => {
      const isCancelled = o.status === 'CANCELLED';
      const hasRefund   = isCancelled && o.coins_spent > 0 && o.payment_method === 'COIN';
      return `
      <div class="admin-order-row">
        <div class="admin-order-no">#${o.order_no}</div>
        <div style="font-size:13px;font-weight:700;">${o.product_name}</div>
        <div class="admin-order-meta">
          ${o.code_name} · ${o.payment_method === 'COIN' ? `◎${o.coins_spent}` : `₹${Number(o.inr_paid/100).toLocaleString('en-IN')}`}
          &nbsp;<span class="status-pill ${o.status}">${o.status}</span>
        </div>
        <div class="admin-order-meta">${o.shipping_name || '—'} · ${o.shipping_phone || ''}</div>
        ${o.shipping_address ? `<div class="admin-order-meta" style="font-size:11px;color:#888;">${o.shipping_address}</div>` : ''}
        <div style="margin-top:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          ${isCancelled
            ? `<div style="font-size:12px;color:var(--danger);font-weight:600;padding:5px 10px;border:1px solid rgba(220,50,50,0.3);border-radius:6px;background:rgba(220,50,50,0.07);">
                 🔒 Cancelled — status locked
               </div>
               ${hasRefund ? `<div style="font-size:11px;color:var(--ok);">✓ ◎${o.coins_spent} refunded to user</div>` : ''}`
            : `<select class="admin-status-select" onchange="adminUpdateOrder('${o.order_no}',this.value)">
                 ${['PENDING','CONFIRMED','SHIPPED','DELIVERED','CANCELLED'].map(s => `<option value="${s}" ${s===o.status?'selected':''}>${s}</option>`).join('')}
               </select>
               <span class="muted small">${o.created_at ? o.created_at.slice(0,16) : ''}</span>`
          }
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    el.innerHTML = `<div style="color:var(--danger)">${e.message||e}</div>`;
  }
}

async function loadAdminUsers() {
  const el = $('adminUserList');
  el.innerHTML = '<div class="muted small">Loading...</div>';
  try {
    const res = await storeApi('/api/admin/users');
    const users = res.users || [];
    if (!users.length) { el.innerHTML = '<div class="muted small">No users.</div>'; return; }
    el.innerHTML = users.map(u => `
      <div class="admin-order-row">
        <div style="font-family:monospace;color:var(--gold2);font-size:12px;">${u.code_name}</div>
        <div style="font-size:13px;">${u.full_name || '—'}</div>
        <div class="admin-order-meta">${u.email||'—'} · ${u.phone||'—'}</div>
        <div class="admin-order-meta">Balance: <strong style="color:var(--gold2)">◎${u.balance}</strong> · Joined: ${u.created_at ? u.created_at.slice(0,10) : ''}</div>
        <div style="display:flex;gap:6px;margin-top:6px;">
          <button class="btn btn-ghost btn-sm" onclick="openCoinAdjustModal('${u.code_name}')">Adjust Coins</button>
          <button class="btn btn-ghost btn-sm" style="color:var(--danger);" onclick="openDeleteUserModal('${u.code_name}')">Remove User</button>
        </div>
      </div>`).join('');
  } catch (e) {
    el.innerHTML = `<div style="color:var(--danger)">${e.message||e}</div>`;
  }
}

// ── Admin Category Management ─────────────────────────────────────────────────

async function loadAdminCategories() {
  const el = $('adminCategoryList');
  if (!el) return;
  el.innerHTML = '<div class="muted small">Loading...</div>';
  try {
    const res = await storeApi('/api/admin/categories');
    const cats = res.categories || [];
    if (!cats.length) { el.innerHTML = '<div class="muted small">No categories yet.</div>'; return; }
    el.innerHTML = cats.map(c => `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
        <div>
          <div style="font-weight:600;color:#eee;">${c.name}</div>
          <div style="font-size:11px;color:#888;font-family:monospace;">${c.slug}</div>
        </div>
        <button class="btn btn-ghost btn-sm" style="color:var(--danger);" onclick="adminDeleteCategory(${c.id},'${c.name.replace(/'/g,"\\'")}')">Delete</button>
      </div>`).join('');
  } catch(e) {
    el.innerHTML = `<div style="color:var(--danger)">${e.message||e}</div>`;
  }
}

async function adminAddCategory() {
  const inp = $('newCategoryName');
  const name = (inp?.value || '').trim();
  const msg  = $('categoryMsg');
  if (!name) { setMsg(msg, 'Category name is required.'); return; }
  const addBtn = document.querySelector('button[onclick="adminAddCategory()"]');
  if (addBtn) { addBtn.disabled = true; addBtn.textContent = 'Adding...'; }
  try {
    await storeApi('/api/admin/category', 'POST', { name });
    inp.value = '';
    setMsg(msg, `Category "${name}" created.`, 'ok');
    await loadAdminCategories();
    await refreshCategoryDropdown();
    // Also refresh store filter buttons
    if (typeof loadStoreProducts === 'function') loadStoreProducts().catch(() => {});
  } catch(e) {
    setMsg(msg, e.message || 'Failed to create category.');
  } finally {
    if (addBtn) { addBtn.disabled = false; addBtn.textContent = '+ Add'; }
  }
}

async function adminDeleteCategory(id, name) {
  if (!confirm(`Delete category "${name}"? Products in this category will be moved to the next available category.`)) return;
  // Disable all delete buttons during operation
  document.querySelectorAll('#adminCategoryList button').forEach(b => { b.disabled = true; });
  setMsg($('categoryMsg'), 'Deleting...', 'muted');
  try {
    const res = await storeApi(`/api/admin/category/${id}`, 'DELETE');
    await loadAdminCategories();
    await refreshCategoryDropdown();
    // Also refresh store filter buttons
    if (typeof loadStoreProducts === 'function') loadStoreProducts().catch(() => {});
    const reassigned = res.products_reassigned_to ? ` Products moved to "${res.products_reassigned_to}".` : '';
    setMsg($('categoryMsg'), `Category "${name}" deleted.${reassigned}`, 'ok');
  } catch(e) {
    setMsg($('categoryMsg'), e.message || 'Delete failed. Please try again.', 'error');
    await loadAdminCategories(); // re-enable buttons by re-rendering
  }
}

async function refreshCategoryDropdown() {
  try {
    const res = await api('/api/categories');
    const cats = res.categories || [];
    const sel  = $('editCategory');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = cats.map(c => `<option value="${c.slug}">${c.name}</option>`).join('');
    if (cats.find(c => c.slug === current)) sel.value = current;
  } catch(e) {}
}

// ── Admin Coin Management ──────────────────────────────────────────────────────

function openCoinAdjustModal(codeNamePrefill) {
  const modal = $('coinAdjustModal');
  if (!modal) return;
  $('coinAdjustCode').value   = codeNamePrefill || '';
  $('coinAdjustAmount').value = '';
  $('coinAdjustNote').value   = '';
  setMsg($('coinAdjustMsg'), '');
  modal.classList.remove('hidden');
}

async function submitCoinAdjust() {
  const code   = ($('coinAdjustCode')?.value  || '').trim().toUpperCase();
  const amount = parseFloat($('coinAdjustAmount')?.value || 0);
  const note   = ($('coinAdjustNote')?.value  || '').trim();
  const msg    = $('coinAdjustMsg');
  if (!code)      { setMsg(msg, 'Code name is required.'); return; }
  if (!amount)    { setMsg(msg, 'Amount cannot be zero.'); return; }
  try {
    const res = await storeApi('/api/admin/user/coins', 'POST', { code_name: code, amount, note });
    setMsg(msg, `Done! New balance: ◎${res.new_balance?.toFixed(4) || '?'}`, 'ok');
    await loadAdminUsers();
  } catch(e) {
    setMsg(msg, e.message || 'Failed.');
  }
}

async function loadCoinLogs() {
  const el = $('adminCoinLogList');
  if (!el) return;
  el.innerHTML = '<div class="muted small">Loading...</div>';
  try {
    const res = await storeApi('/api/admin/user/coin-logs');
    const logs = res.logs || [];
    if (!logs.length) { el.innerHTML = '<div class="muted small">No logs yet.</div>'; return; }
    el.innerHTML = logs.map(l => `
      <div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:12px;">
        <div style="display:flex;justify-content:space-between;">
          <span style="color:var(--gold2);">${l.target}</span>
          <span style="color:${l.amount >= 0 ? 'var(--ok)' : 'var(--danger)'};">${l.amount >= 0 ? '+' : ''}${l.amount} ◎</span>
        </div>
        <div class="muted" style="font-size:11px;">${l.note || '—'} · by ${l.admin} · ${l.created_at?.slice(0,16) || ''}</div>
      </div>`).join('');
  } catch(e) {
    el.innerHTML = `<div style="color:var(--danger)">${e.message||e}</div>`;
  }
}

// ── Admin Delete User ──────────────────────────────────────────────────────────

let _deleteUserTarget = '';

function openDeleteUserModal(codeName) {
  _deleteUserTarget = codeName;
  $('deleteUserTarget').textContent = `User: ${codeName}`;
  setMsg($('deleteUserMsg'), '');
  $('deleteUserModal').classList.remove('hidden');
}

async function confirmDeleteUser() {
  if (!_deleteUserTarget) return;
  const msg = $('deleteUserMsg');
  try {
    await storeApi(`/api/admin/user/${_deleteUserTarget}`, 'DELETE');
    $('deleteUserModal').classList.add('hidden');
    await loadAdminUsers();
    alert(`User ${_deleteUserTarget} permanently removed.`);
    _deleteUserTarget = '';
  } catch(e) {
    setMsg(msg, e.message || 'Delete failed.', '');
  }
}

// ── Admin Drop All Tables ──────────────────────────────────────────────────────

async function confirmDropAllTables() {
  const confirm_val = ($('dropTablesConfirm')?.value || '').trim();
  const msg = $('dropTablesMsg');
  if (confirm_val !== 'DROP ALL TABLES') {
    setMsg(msg, "Type exactly: DROP ALL TABLES"); return;
  }
  try {
    const res = await storeApi('/api/admin/database/drop-all', 'DELETE', { confirm: confirm_val });
    $('dropTablesModal').classList.add('hidden');
    alert(`All tables dropped (${res.tables_dropped?.length || 0} tables). The database will be re-initialised on next request.`);
  } catch(e) {
    setMsg(msg, e.message || 'Failed to drop tables.');
  }
}

async function loadAdminSettings() {
  try {
    const res = await storeApi('/api/admin/settings');
    const s = res.settings || {};
    ['coin_to_inr','service_charge_inr','service_charge_coins','store_name','razorpay_enabled','coins_enabled'].forEach(k => {
      const el = $('set-' + k);
      if (el && s[k] !== undefined) el.value = s[k];
    });
  } catch (e) {}
}

async function adminUpdateOrder(orderNo, status) {
  try {
    const res = await storeApi(`/api/admin/order/${orderNo}`, 'PUT', { status });
    // Always reload so the UI reflects the latest status
    await loadAdminOrders();
    if (status === 'CANCELLED' && res.refunded && res.refund_coins > 0) {
      const notice = document.createElement('div');
      notice.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1a2a1a;border:1px solid var(--ok);color:var(--ok);padding:10px 20px;border-radius:8px;font-size:13px;z-index:9999;';
      notice.textContent = `✓ Order #${orderNo} cancelled — ◎${res.refund_coins} refunded to user.`;
      document.body.appendChild(notice);
      setTimeout(() => notice.remove(), 4000);
    }
    if (status === 'DELIVERED') {
      const notice = document.createElement('div');
      notice.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1a1a2a;border:1px solid var(--gold2);color:var(--gold2);padding:10px 20px;border-radius:8px;font-size:13px;z-index:9999;';
      notice.textContent = `✓ Order #${orderNo} marked delivered — invoice emailed to customer.`;
      document.body.appendChild(notice);
      setTimeout(() => notice.remove(), 4500);
    }
  } catch (e) {
    alert(e.message || 'Update failed.');
    await loadAdminOrders();
  }
}

async function adminDeleteProduct(id, name) {
  if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
  try {
    await storeApi(`/api/admin/product/${id}`, 'DELETE');
    await loadAdminProducts();
  } catch (e) {
    alert('Delete failed: ' + (e.message || e));
  }
}

// ── Product Edit Modal ────────────────────────────────────────────────────────

function openProductEditModal(product) {
  const modal = $('productEditModal');
  $('productEditTitle').textContent = product && product.id ? 'Edit Product' : 'Add Product';
  $('editProductId').value = product && product.id ? product.id : '';
  $('editName').value         = product ? product.name        || '' : '';
  $('editCategory').value     = product ? product.category    || '' : '';
  $('editPriceCoins').value   = product ? product.price_coins || 1.0 : 1.0;
  $('editPriceInr').value     = product ? product.price_inr   || 10000 : 10000;
  $('editStock').value        = product ? product.stock        || 10 : 10;
  $('editImageUrl').value     = product ? product.image_url   || '' : '';
  if ($('editCoinsEnabled')) $('editCoinsEnabled').value = (product && product.coins_enabled === false) ? '0' : '1';
  if ($('editInrEnabled'))   $('editInrEnabled').value   = (product && product.inr_enabled   === false) ? '0' : '1';
  // Update inline image preview
  const prevUrl = product?.image_url || '';
  const pv = $('editImgPreview');
  const pvImg = $('editImgPreviewImg');
  if (pv && pvImg) { if (prevUrl) { pvImg.src = prevUrl; pv.style.display = ''; } else pv.style.display = 'none'; }
  $('editDescription').value  = product ? product.description || '' : '';
  $('editFeatures').value     = product ? product.features    || '' : '';
  $('editActive').value       = product && !product.active ? '0' : '1';
  $('productEditMsg').textContent = '';
  $('productEditMsg').style.display = 'none';
  // Load dynamic categories into dropdown
  refreshCategoryDropdown().then(() => {
    if (product?.category) $('editCategory').value = product.category;
  });
  modal.classList.remove('hidden');
}

async function saveProduct() {
  const id = $('editProductId').value;
  const payload = {
    name:          ($('editName').value || '').trim(),
    category:      $('editCategory').value,
    price_coins:   parseFloat($('editPriceCoins').value) || 1.0,
    price_inr:     parseInt($('editPriceInr').value)     || 10000,
    stock:         parseInt($('editStock').value)         || 0,
    image_url:     ($('editImageUrl').value    || '').trim(),
    description:   ($('editDescription').value || '').trim(),
    features:      ($('editFeatures').value    || '').trim(),
    active:        parseInt($('editActive').value),
    coins_enabled: parseInt($('editCoinsEnabled')?.value ?? '1'),
    inr_enabled:   parseInt($('editInrEnabled')?.value   ?? '1'),
  };
  if (!payload.name) { showStoreMsg('productEditMsg', 'Name is required'); return; }
  try {
    const url    = id ? `/api/admin/product/${id}` : '/api/admin/product';
    const method = id ? 'PUT' : 'POST';
    await storeApi(url, method, payload);
    $('productEditModal').classList.add('hidden');
    await loadAdminProducts();
    await loadStoreProducts();
  } catch (e) {
    showStoreMsg('productEditMsg', e.message || 'Save failed');
  }
}

// ── Delete Account Modal ──────────────────────────────────────────────────────

function openDeleteAccountModal() {
  if (!state.me) return;
  const modal = $('deleteAccountModal');
  $('deleteConfirmCode').value = '';
  $('deleteConfirmPass').value = '';
  $('deleteFinalCheck').checked = false;
  $('deleteModalMsg').style.display = 'none';
  $('deleteCodeHint').textContent = 'Must match exactly';
  $('deleteCodeHint').className = '';
  $('btnConfirmDelete').disabled = true;
  $('btnConfirmDelete').style.opacity = '0.4';
  $('btnConfirmDelete').style.cursor  = 'not-allowed';

  const bal = state.me.coinvalue;
  const warn = $('deleteBalanceWarn');
  warn.innerHTML = bal > 0
    ? `<span style="color:var(--danger)">⚠ You have ◎${bal} coins. Transfer or spend all coins before deleting.</span>`
    : `<span style="color:var(--ok)">✓ Balance is ◎0 — eligible for deletion.</span>`;

  modal.classList.remove('hidden');
}

function checkDeleteReady() {
  const code    = ($('deleteConfirmCode').value || '').trim().toUpperCase();
  const pass    = ($('deleteConfirmPass').value || '').trim();
  const checked = $('deleteFinalCheck').checked;
  const hint    = $('deleteCodeHint');
  const btn     = $('btnConfirmDelete');
  const myCode  = state.me ? state.me.code_name.toUpperCase() : '';

  if (code.length > 0) {
    if (code === myCode) {
      hint.textContent = '✓ Code name matches';
      hint.className = 'hint-ok';
    } else {
      hint.textContent = '✗ Does not match your code name';
      hint.className = 'hint-bad';
    }
  } else {
    hint.textContent = 'Must match exactly';
    hint.className = '';
  }

  const ready = (code === myCode) && pass.length >= 12 && checked;
  btn.disabled = !ready;
  btn.style.opacity = ready ? '1' : '0.4';
  btn.style.cursor  = ready ? 'pointer' : 'not-allowed';
}

async function confirmDeleteAccount() {
  const code    = ($('deleteConfirmCode').value || '').trim().toUpperCase();
  const pass    = ($('deleteConfirmPass').value || '').trim();
  const checked = $('deleteFinalCheck').checked;
  const msgEl   = $('deleteModalMsg');
  const myCode  = state.me ? state.me.code_name.toUpperCase() : '';

  if (code !== myCode)         { msgEl.textContent = 'Code name does not match.'; msgEl.style.color = 'var(--danger)'; msgEl.style.display = 'block'; return; }
  if (!pass)                   { msgEl.textContent = 'Password is required.';     msgEl.style.color = 'var(--danger)'; msgEl.style.display = 'block'; return; }
  if (!checked)                { msgEl.textContent = 'Please tick the checkbox.'; msgEl.style.color = 'var(--danger)'; msgEl.style.display = 'block'; return; }

  const btn = $('btnConfirmDelete');
  btn.disabled = true; btn.textContent = 'Deleting...';

  try {
    await storeApi('/api/account/delete', 'DELETE', { password: pass });
    $('deleteAccountModal').classList.add('hidden');
    state.me = null; state.receiver = null;
    showScreen('auth');
    $('loginStatus').textContent = 'Account deleted.';
  } catch (e) {
    msgEl.textContent = e.message || 'Deletion failed.';
    msgEl.style.color = 'var(--danger)';
    msgEl.style.display = 'block';
    btn.disabled = false;
    btn.textContent = 'Delete Permanently';
  }
}

// ── Wire up all store events when DOM loads ───────────────────────────────────

function bindStoreEvents() {
  // Store button on dashboard
  const btnStore = $('btnStore');
  if (btnStore) btnStore.addEventListener('click', () => openStore());

  // Back from store
  const btnBack = $('btnBackToDashFromStore');
  if (btnBack) btnBack.addEventListener('click', async () => { showScreen('dashboard'); await refreshDashboard(); });

  // Back to store grid from detail
  const btnBackStore = $('btnBackToStore');
  if (btnBackStore) btnBackStore.addEventListener('click', () => {
    $('storeMain').style.display = 'block';
    $('storeDetail').style.display = 'none';
  });

  // Admin panel button
  const btnAdmin = $('btnAdminPanel');
  if (btnAdmin) btnAdmin.addEventListener('click', () => openAdminPanel());

  // Back from admin
  const btnBackAdmin = $('btnBackFromAdmin');
  if (btnBackAdmin) btnBackAdmin.addEventListener('click', () => openStore());

  // Admin tabs
  document.querySelectorAll('.admin-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchAdminTab(btn.dataset.atab));
  });

  // Add product
  const btnAddProd = $('btnAddProduct');
  if (btnAddProd) btnAddProd.addEventListener('click', () => openProductEditModal(null));

  // Save product
  const btnSaveProd = $('btnSaveProduct');
  if (btnSaveProd) btnSaveProd.addEventListener('click', saveProduct);

  // Cancel product edit
  const btnCancelEdit = $('btnCancelProductEdit');
  if (btnCancelEdit) btnCancelEdit.addEventListener('click', () => $('productEditModal').classList.add('hidden'));
  const btnCloseEdit = $('btnCloseProductEdit');
  if (btnCloseEdit) btnCloseEdit.addEventListener('click', () => $('productEditModal').classList.add('hidden'));

  // Refresh admin orders / users
  const btnRefOrders = $('btnRefreshOrders');
  if (btnRefOrders) btnRefOrders.addEventListener('click', loadAdminOrders);
  const btnRefUsers = $('btnRefreshUsers');
  if (btnRefUsers) btnRefUsers.addEventListener('click', loadAdminUsers);

  // Save settings
  const btnSaveSet = $('btnSaveSettings');
  if (btnSaveSet) btnSaveSet.addEventListener('click', async () => {
    const keys = ['coin_to_inr','service_charge_inr','service_charge_coins','store_name','razorpay_enabled','coins_enabled'];
    const payload = {};
    keys.forEach(k => { const el = $('set-' + k); if (el) payload[k] = el.value; });
    try {
      await storeApi('/api/admin/settings', 'POST', payload);
      showStoreMsg('settingsMsg', 'Settings saved!', 'ok');
      await loadStoreProducts();
    } catch (e) {
      showStoreMsg('settingsMsg', e.message || 'Save failed');
    }
  });

  // Category filter
  const catBtns = $('storeCatBtns');
  if (catBtns) {
    catBtns.addEventListener('click', e => {
      const btn = e.target.closest('.store-cat-btn');
      if (!btn) return;
      catBtns.querySelectorAll('.store-cat-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterStoreProducts(btn.dataset.cat);
    });
  }

  // My orders button
  const btnMyOrders = $('btnMyOrders');
  if (btnMyOrders) btnMyOrders.addEventListener('click', openMyOrdersModal);
  const btnCloseMyOrders = $('btnCloseMyOrders');
  if (btnCloseMyOrders) btnCloseMyOrders.addEventListener('click', () => $('myOrdersModal').classList.add('hidden'));
  const myOrdersModal = $('myOrdersModal');
  if (myOrdersModal) myOrdersModal.addEventListener('click', e => { if (e.target === myOrdersModal) myOrdersModal.classList.add('hidden'); });

  // Delete account button
  const btnDel = $('btnDeleteAccount');
  if (btnDel) btnDel.addEventListener('click', openDeleteAccountModal);

  // Delete modal events
  const deleteConfirmCode = $('deleteConfirmCode');
  if (deleteConfirmCode) deleteConfirmCode.addEventListener('input', checkDeleteReady);
  const deleteConfirmPass = $('deleteConfirmPass');
  if (deleteConfirmPass) deleteConfirmPass.addEventListener('input', checkDeleteReady);
  const deleteFinalCheck = $('deleteFinalCheck');
  if (deleteFinalCheck) deleteFinalCheck.addEventListener('change', checkDeleteReady);
  const btnConfirmDel = $('btnConfirmDelete');
  if (btnConfirmDel) btnConfirmDel.addEventListener('click', confirmDeleteAccount);
  const btnCancelDel = $('btnCancelDelete');
  if (btnCancelDel) btnCancelDel.addEventListener('click', () => $('deleteAccountModal').classList.add('hidden'));
  const btnCloseDel = $('btnCloseDeleteModal');
  if (btnCloseDel) btnCloseDel.addEventListener('click', () => $('deleteAccountModal').classList.add('hidden'));
  const delModal = $('deleteAccountModal');
  if (delModal) delModal.addEventListener('click', e => { if (e.target === delModal) delModal.classList.add('hidden'); });

  // Product edit modal close on backdrop
  const pEditModal = $('productEditModal');
  if (pEditModal) pEditModal.addEventListener('click', e => { if (e.target === pEditModal) pEditModal.classList.add('hidden'); });
}

// Patch boot() so store screens are registered at the same time as the rest
const _origBoot = boot;
boot = function() {
  _origBoot();
  screens['store'] = $('screen-store');
  screens['admin'] = $('screen-admin');
  screens['home']    = $('screen-home');
  screens['profile'] = $('screen-profile');
  screens['connect'] = $('screen-connect');
  [$('screen-store'), $('screen-admin'), $('screen-home'), $('screen-profile'), $('screen-connect')]
    .forEach(el => el && el.classList.add('hidden'));
  bindStoreEvents();
  bindProfileEvents();
  bindConnectEvents();
};

// ═══════════════════════════════════════════════════════════════════════════════
// END NOVA WATCH STORE
// ═══════════════════════════════════════════════════════════════════════════════

// =============================================================================
// HOME SCREEN — public profiles
// =============================================================================

async function openHomeScreen() {
  showScreen('home');
  const grid = $('homeProfilesGrid');
  if (!grid) return;
  grid.innerHTML = '<div class="muted small">Loading...</div>';
  try {
    const data = await api('/api/profiles/public');
    if (!data.profiles || !data.profiles.length) {
      grid.innerHTML = '<div class="muted small" style="padding:20px 0;">No public profiles yet.</div>';
      return;
    }
    grid.innerHTML = '';
    grid.style.cssText = 'display:block;';

    data.profiles.forEach(p => {
      const card = document.createElement('div');
      card.className = 'nova-home-profile-card';
      card.onclick = () => openProfileViewModal(p.code_name);

      // Left: circular avatar
      const avatarWrap = document.createElement('div');
      avatarWrap.className = 'nova-home-avatar-wrap';
      if (p.photo_url) {
        const img = document.createElement('img');
        img.src = p.photo_url;
        img.alt = p.code_name;
        img.onerror = () => { avatarWrap.innerHTML = (p.code_name || '?').charAt(0).toUpperCase(); };
        avatarWrap.appendChild(img);
      } else {
        avatarWrap.textContent = (p.code_name || '?').charAt(0).toUpperCase();
      }

      // Right: info block
      const info = document.createElement('div');
      info.className = 'nova-home-info';

      const codeName = document.createElement('div');
      codeName.className = 'nova-home-code-name';
      codeName.textContent = p.code_name || '';

      const accNum = document.createElement('div');
      accNum.className = 'nova-home-account-num';
      accNum.textContent = p.coin ? `Acc: ${p.coin}` : '';

      info.appendChild(codeName);
      if (p.coin) info.appendChild(accNum);

      // Detail rows
      const addDetailRow = (label, val) => {
        if (!val) return;
        const row = document.createElement('div');
        row.className = 'nova-home-detail-row';
        row.innerHTML = `<strong>${label}:</strong> ${val}`;
        info.appendChild(row);
      };
      if (p.email)         addDetailRow('Email', p.email);
      if (p.phone)         addDetailRow('Phone', p.phone);
      if (p.work_title)    addDetailRow('Title', p.work_title);
      if (p.work_company)  addDetailRow('Company', p.work_company);

      if (p.bio) {
        const bio = document.createElement('div');
        bio.className = 'nova-home-bio';
        bio.textContent = p.bio;
        info.appendChild(bio);
      }

      card.appendChild(avatarWrap);
      card.appendChild(info);
      grid.appendChild(card);
    });
  } catch(e) {
    grid.innerHTML = `<div style="color:var(--danger);font-size:13px;">${e.message}</div>`;
  }
}

function avatarLetterHtml(codeName) {
  return `<div style="width:60%;height:60%;border-radius:50%;background:var(--gold);display:flex;align-items:center;justify-content:center;color:#000;font-weight:700;font-size:clamp(18px,4vw,28px);">${(codeName||'?').charAt(0)}</div>`;
}

// ── Profile View Modal ─────────────────────────────────────────────────────────
let _pvTargetCode = '';

function openProfileViewModal(codeName) {
  if (!codeName) return;
  // If own profile, open profile screen
  if (state.me && codeName === state.me.code_name) { openProfileScreen(); return; }

  const modal = $('profileViewModal');
  if (!modal) return;

  _pvTargetCode = codeName;

  // Reset state
  $('pvCodeName').textContent  = codeName;
  $('pvWorkTitle').textContent = '';
  $('pvDetails').innerHTML     = '';
  $('pvBio').style.display     = 'none';
  $('pvBio').textContent       = '';
  $('pvLoading').style.display = '';
  $('pvMsg').textContent       = '';
  $('pvMsg').className         = 'msg';
  $('pvPhoto').style.display   = 'none';
  $('pvInitial').style.display = '';
  $('pvInitial').textContent   = (codeName || '?').charAt(0).toUpperCase();
  $('pvConnectBtn').textContent = 'Connect';
  $('pvConnectBtn').disabled    = false;

  modal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';

  // Load profile data
  api(`/api/profile/${codeName}`).then(data => {
    $('pvLoading').style.display = 'none';
    if (!data || !data.ok) return;

    $('pvCodeName').textContent  = data.code_name || codeName;
    $('pvWorkTitle').textContent = data.work_title || '';

    if (data.photo_url) {
      const img = $('pvPhoto');
      img.src = data.photo_url;
      img.onerror = () => { img.style.display = 'none'; $('pvInitial').style.display = ''; };
      img.onload  = () => { img.style.display = 'block'; $('pvInitial').style.display = 'none'; };
    }

    const details = $('pvDetails');
    details.innerHTML = '';
    const addDetail = (icon, val) => {
      if (!val) return;
      const d = document.createElement('div');
      d.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:13px;color:#ccc;';
      d.innerHTML = `<span style="color:var(--gold2);font-size:11px;min-width:14px;">${icon}</span><span>${val}</span>`;
      details.appendChild(d);
    };
    addDetail('Company: ', data.work_company);
    addDetail('Location: ', data.work_location);

    if (data.bio) {
      const bioEl = $('pvBio');
      bioEl.textContent = data.bio;
      bioEl.style.display = '';
    }
  }).catch(err => {
    $('pvLoading').style.display = 'none';
    setMsg($('pvMsg'), err.message || 'Could not load profile.', '');
  });
}

function closeProfileViewModal() {
  $('profileViewModal')?.classList.add('hidden');
  document.body.style.overflow = '';
  _pvTargetCode = '';
}

async function pvSendConnect() {
  if (!state.me) { closeProfileViewModal(); showScreen('auth'); return; }
  if (!_pvTargetCode) return;
  const btn = $('pvConnectBtn');
  btn.disabled = true;
  btn.textContent = 'Sending…';
  try {
    await api('/api/connect/request', 'POST', { code_name: _pvTargetCode });
    btn.textContent = 'Sent ✓';
    setMsg($('pvMsg'), 'Connection request sent!', 'ok');
  } catch(e) {
    btn.disabled = false;
    btn.textContent = 'Connect';
    setMsg($('pvMsg'), e.message || 'Could not send request.', '');
  }
}

// Close modal on background click
document.addEventListener('click', e => {
  if (e.target === $('profileViewModal')) closeProfileViewModal();
});

// =============================================================================
// PROFILE SCREEN
// =============================================================================

function bindProfileEvents() {
  $('btnBackFromProfile')?.addEventListener('click', () => showScreen('dashboard'));
  $('btnBackFromHome')?.addEventListener('click',    () => showScreen('dashboard'));
  $('btnSaveProfile')?.addEventListener('click', saveProfile);
}

async function openProfileScreen() {
  showScreen('profile');
  const msg = $('profileMsg');
  setMsg(msg, '');
  try {
    const data = await api('/api/profile/me');
    if ($('profileCodeName'))     $('profileCodeName').textContent     = data.code_name || '';
    if ($('profileBio'))          $('profileBio').value                = data.bio || '';
    if ($('profileWorkTitle'))    $('profileWorkTitle').value           = data.work_title || '';
    if ($('profileWorkCompany'))  $('profileWorkCompany').value         = data.work_company || '';
    if ($('profileWorkLocation')) $('profileWorkLocation').value        = data.work_location || '';
    if ($('profileIsPublic'))     $('profileIsPublic').checked          = !!data.is_public;
    // photo
    _profilePhotoUrl = data.photo_url || '';
    renderProfilePhotoPreview(_profilePhotoUrl, data.code_name || '?');
    // pending badge
    const pending = data.pending_connections || 0;
    updateConnBadges(pending);
    if (pending > 0) { $('pendingConnCard').style.display = ''; loadPendingRequests(); }
    else               $('pendingConnCard').style.display = 'none';
  } catch(e) {
    setMsg(msg, e.message);
  }
}

async function saveProfile() {
  const btn = $('btnSaveProfile');
  const msg = $('profileMsg');
  btn.disabled = true;
  setMsg(msg, '');
  try {
    await api('/api/profile/save', 'POST', {
      bio:           ($('profileBio')?.value || '').trim(),
      work_title:    ($('profileWorkTitle')?.value || '').trim(),
      work_company:  ($('profileWorkCompany')?.value || '').trim(),
      work_location: ($('profileWorkLocation')?.value || '').trim(),
      is_public:     !!$('profileIsPublic')?.checked,
      photo_url:     _profilePhotoUrl || '',
    });
    setMsg(msg, 'Profile saved.', 'ok');
  } catch(e) {
    setMsg(msg, e.message);
  } finally {
    btn.disabled = false;
  }
}

async function loadPendingRequests() {
  const list = $('pendingConnList');
  if (!list) return;
  list.innerHTML = '<div class="muted small">Loading...</div>';
  try {
    const data = await api('/api/connections');
    if (!data.pending || !data.pending.length) {
      $('pendingConnCard').style.display = 'none';
      return;
    }
    $('pendingConnCount').textContent = data.pending.length;
    list.innerHTML = data.pending.map(p => `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
        <span style="color:#ddd;font-size:13px;">${p.code_name}</span>
        <div style="display:flex;gap:6px;">
          <button class="btn btn-gold btn-sm" onclick="respondConn(${p.id},'ACCEPT')">Accept</button>
          <button class="btn btn-ghost btn-sm" onclick="respondConn(${p.id},'DECLINE')">Decline</button>
        </div>
      </div>
    `).join('');
  } catch(e) {
    list.innerHTML = `<div class="muted small" style="color:var(--danger);">${e.message}</div>`;
  }
}

async function respondConn(id, action) {
  try {
    await api('/api/connect/respond', 'POST', { id, action });
    // Refresh profile card if visible
    if ($('pendingConnCard') && $('pendingConnCard').style.display !== 'none') {
      loadPendingRequests();
    }
    // Refresh connect screen lists (safe even if screen not active)
    await loadConnectionsList();
    if (action === 'ACCEPT') switchConnMainTab('connected');
  } catch(e) {
    alert(e.message);
  }
}

function updateConnBadges(count) {
  const show = count > 0;
  [$('profileConnBadge'), $('drawerProfileBadge'), $('drawerConnBadge')].forEach(el => {
    if (!el) return;
    if (show) { el.textContent = count; el.classList.remove('hidden'); }
    else el.classList.add('hidden');
  });
}

// =============================================================================
// CONNECT SCREEN
// =============================================================================

let _chatPeerId   = null;
let _chatPeerName = null;
let _chatPollTimer = null;
let _chatLastTs    = 0;

function bindConnectEvents() {
  $('btnBackFromConnect')?.addEventListener('click', () => showScreen('dashboard'));
  $('btnCloseChatView')?.addEventListener('click', closeChatView);

  const sendBtn = $('btnSendMsg');
  if (sendBtn) {
    // touchstart + preventDefault cancels the synthetic click Android fires after tap.
    // This means on mobile: only touchstart fires sendChatMessage (never click).
    // On desktop (no touch): only click fires.
    sendBtn.addEventListener('touchstart', e => {
      e.preventDefault();
      e.stopImmediatePropagation();
      sendChatMessage();
    }, { passive: false });
    sendBtn.addEventListener('click', e => {
      // On Android, if touchstart already fired, this click is synthetic — skip it.
      // We detect this by checking if the event has no pointer (isTrusted but no coords change).
      if (e.sourceCapabilities && !e.sourceCapabilities.firesTouchEvents) {
        sendChatMessage(); // real desktop mouse click only
      }
    });
  }

  const chatInp = $('chatInput');
  if (chatInp) {
    chatInp.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); sendChatMessage(); }
    });
  }

  $('btnBackFromProfile')?.addEventListener('click', () => showScreen('dashboard'));
}

// ─── Tab state ────────────────────────────────────────────────────
let _connMainTab = 'requests';   // 'requests' | 'connected'
let _connSubTab  = 'sent';       // 'sent' | 'received'

// unread counts per peer_id (ephemeral, cleared when chat opened)
const _connUnread = {};          // { user_id: count }
const _connLastMsg = {};         // { user_id: { text, ts } }

function switchConnMainTab(tab) {
  _connMainTab = tab;
  $('connTabRequests')?.classList.toggle('active', tab === 'requests');
  $('connTabConnected')?.classList.toggle('active', tab === 'connected');
  $('connPanelRequests').style.display  = tab === 'requests'  ? '' : 'none';
  $('connPanelConnected').style.display = tab === 'connected' ? '' : 'none';
}

function switchConnSubTab(sub) {
  _connSubTab = sub;
  $('connSubSent')?.classList.toggle('active', sub === 'sent');
  $('connSubReceived')?.classList.toggle('active', sub === 'received');
  $('connSentPanel').style.display     = sub === 'sent'     ? '' : 'none';
  $('connReceivedPanel').style.display = sub === 'received' ? '' : 'none';
}

function _avatarLetter(name) {
  return (name || '?').charAt(0).toUpperCase();
}

// Renders an avatar: photo if available, else letter fallback
function _avatarHtml(codeName, photoUrl, cls) {
  const letter = _avatarLetter(codeName);
  const className = cls || 'conn-req-avatar';
  if (photoUrl) {
    return `<div class="${className}" style="overflow:hidden;padding:0;">` +
      `<img src="${photoUrl}" alt="${letter}" ` +
      `style="width:100%;height:100%;object-fit:cover;border-radius:50%;" ` +
      `onerror="this.parentElement.innerHTML='${letter}';this.parentElement.style.padding='';"/>` +
      `</div>`;
  }
  return `<div class="${className}">${letter}</div>`;
}

function _relTime(ts) {
  if (!ts) return '';
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

async function openConnectScreen(highlightCode) {
  showScreen('connect');
  switchConnMainTab('requests');
  switchConnSubTab('sent');
  await loadConnectionsList(highlightCode);
  startBgPoll();
}

async function loadConnectionsList(highlightCode) {
  try {
    const data = await api('/api/connections');

    // ── CONNECTED (DM list) ──────────────────────────────────────
    const connList = $('connectList');
    if (connList) {
      if (!data.accepted.length) {
        connList.innerHTML = '<div class="conn-empty-state">No connections yet</div>';
      } else {
        // Sort: unread first, then by last message ts desc, then by code_name
        const sorted = [...data.accepted].sort((a, b) => {
          const ua = _connUnread[a.user_id] || 0;
          const ub = _connUnread[b.user_id] || 0;
          if (ua !== ub) return ub - ua;
          const ta = (_connLastMsg[a.user_id]?.ts || 0);
          const tb = (_connLastMsg[b.user_id]?.ts || 0);
          if (ta !== tb) return tb - ta;
          return a.code_name.localeCompare(b.code_name);
        });
        connList.innerHTML = sorted.map(c => {
          const unread = _connUnread[c.user_id] || 0;
          const last = _connLastMsg[c.user_id];
          const preview = last ? last.text.substring(0, 48) + (last.text.length > 48 ? '…' : '') : 'Tap to start chatting';
          const ts = last ? _relTime(last.ts) : '';
          return `
          <div class="conn-dm-row${unread ? ' unread' : ''}" data-peer-id="${c.user_id}">
            <div class="conn-dm-avatar-wrap" onclick="openChatWith('${c.user_id}','${c.code_name}')">
              ${c.photo_url
                ? `<div class="conn-dm-avatar" style="overflow:hidden;padding:0;"><img src="${c.photo_url}" alt="${_avatarLetter(c.code_name)}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;" onerror="this.parentElement.innerHTML='${_avatarLetter(c.code_name)}';this.parentElement.style.padding='';"/></div>`
                : `<div class="conn-dm-avatar">${_avatarLetter(c.code_name)}</div>`}
            </div>
            <div class="conn-dm-body" onclick="openChatWith('${c.user_id}','${c.code_name}')">
              <div class="conn-dm-name">${c.code_name}</div>
              <div class="conn-dm-preview">${preview}</div>
            </div>
            <div class="conn-dm-right">
              ${ts ? `<span class="conn-dm-time">${ts}</span>` : ''}
              ${unread
                ? `<span class="conn-unread-badge">${unread}</span>`
                : `<button class="btn btn-ghost btn-sm" style="font-size:10px;padding:2px 7px;color:rgba(255,60,60,0.6);border-color:rgba(255,60,60,0.15);" onclick="removeConn(${c.id})">Remove</button>`
              }
            </div>
          </div>`;
        }).join('');
      }
      // update tab badge
      const cb = $('connConBadge');
      if (cb) { cb.textContent = data.accepted.length; cb.classList.toggle('hidden', !data.accepted.length); }
    }

    // ── SENT REQUESTS ────────────────────────────────────────────
    const sentList = $('sentList');
    if (sentList) {
      if (!data.sent.length) {
        sentList.innerHTML = '<div class="conn-empty-state">No sent requests</div>';
      } else {
        sentList.innerHTML = data.sent.map(s => `
          <div class="conn-req-row">
            ${s.photo_url
              ? `<div class="conn-req-avatar" style="overflow:hidden;padding:0;"><img src="${s.photo_url}" alt="${_avatarLetter(s.code_name)}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;" onerror="this.parentElement.innerHTML='${_avatarLetter(s.code_name)}';this.parentElement.style.padding='';"/></div>`
              : `<div class="conn-req-avatar">${_avatarLetter(s.code_name)}</div>`}
            <div class="conn-req-info">
              <div class="conn-req-name">${s.code_name}</div>
              <div class="conn-req-meta">Waiting for response</div>
            </div>
            <div class="conn-req-right">
              <span class="conn-pending-chip">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                Pending
              </span>
              <button class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 8px;color:var(--danger);border-color:rgba(255,60,60,0.25);" onclick="deleteConnRequest(${s.id})">Delete</button>
            </div>
          </div>
        `).join('');
      }
    }

    // ── RECEIVED (PENDING) REQUESTS ──────────────────────────────
    const pendingList = $('connReceivedList');
    if (pendingList) {
      if (!data.pending || !data.pending.length) {
        pendingList.innerHTML = '<div class="conn-empty-state">No pending requests</div>';
        const rb = $('connReceivedBadge'); if (rb) rb.classList.add('hidden');
        const nb = $('connReqBadge');     if (nb) nb.classList.add('hidden');
      } else {
        const rb = $('connReceivedBadge');
        if (rb) { rb.textContent = data.pending.length; rb.classList.remove('hidden'); }
        const nb = $('connReqBadge');
        if (nb) { nb.textContent = data.pending.length; nb.classList.remove('hidden'); }
        pendingList.innerHTML = data.pending.map(p => `
          <div class="conn-req-row">
            ${p.photo_url
              ? `<div class="conn-req-avatar" style="overflow:hidden;padding:0;"><img src="${p.photo_url}" alt="${_avatarLetter(p.code_name)}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;" onerror="this.parentElement.innerHTML='${_avatarLetter(p.code_name)}';this.parentElement.style.padding='';"/></div>`
              : `<div class="conn-req-avatar">${_avatarLetter(p.code_name)}</div>`}
            <div class="conn-req-info">
              <div class="conn-req-name">${p.code_name}</div>
              <div class="conn-req-meta">Wants to connect</div>
            </div>
            <div class="conn-req-right">
              <div class="conn-req-actions">
                <button class="btn btn-gold btn-sm" style="font-size:11px;padding:4px 12px;" onclick="respondConn(${p.id},'ACCEPT')">Accept</button>
                <button class="btn btn-ghost btn-sm" style="font-size:11px;padding:4px 10px;" onclick="respondConn(${p.id},'DECLINE')">Decline</button>
              </div>
            </div>
          </div>
        `).join('');
      }
    }

    // also refresh profile screen pending card
    if (data.pending && data.pending.length > 0) {
      const pc = $('pendingConnCard');
      if (pc) { pc.style.display = ''; $('pendingConnCount').textContent = data.pending.length; }
    } else {
      const pc = $('pendingConnCard'); if (pc) pc.style.display = 'none';
    }

    // highlight code logic (auto send banner)
    if (highlightCode && data.accepted.every(c => c.code_name !== highlightCode)) {
      promptConnectRequest(highlightCode, data);
    }
  } catch(e) {
    const cl = $('connectList');
    if (cl) cl.innerHTML = `<div style="color:var(--danger);font-size:13px;padding:14px;">${e.message}</div>`;
  }
}

async function deleteConnRequest(id) {
  if (!confirm('Delete this sent request?')) return;
  try {
    await api(`/api/connect/${id}`, 'DELETE');
    loadConnectionsList();
  } catch(e) { alert(e.message); }
}

function promptConnectRequest(codeName, existingData) {
  const alreadySent = existingData.sent.some(s => s.code_name === codeName);
  if (alreadySent) return;
  // Show banner at top of sent panel
  const sentList = $('sentList');
  if (!sentList) return;
  const banner = document.createElement('div');
  banner.className = 'card';
  banner.style.cssText = 'margin:12px 14px;border-color:rgba(212,175,55,0.4);';
  banner.innerHTML = `
    <div style="font-size:13px;margin-bottom:8px;">Connect with <strong style="color:var(--gold);">${codeName}</strong>?</div>
    <button class="btn btn-gold btn-sm" id="btnSendReqBanner">Send Request</button>
    <div id="reqBannerMsg" class="msg" style="margin-top:6px;"></div>
  `;
  sentList.parentElement.insertBefore(banner, sentList);
  $('btnSendReqBanner').addEventListener('click', async () => {
    $('btnSendReqBanner').disabled = true;
    try {
      await api('/api/connect/request', 'POST', { code_name: codeName });
      setMsg($('reqBannerMsg'), 'Request sent!', 'ok');
      setTimeout(() => banner.remove(), 2000);
      loadConnectionsList();
    } catch(e) {
      setMsg($('reqBannerMsg'), e.message);
      $('btnSendReqBanner').disabled = false;
    }
  });
}

async function removeConn(id) {
  if (!confirm('Remove this connection?')) return;
  try {
    await api(`/api/connect/${id}`, 'DELETE');
    loadConnectionsList();
  } catch(e) { alert(e.message); }
}

// =============================================================================
// EPHEMERAL CHAT
// =============================================================================
//
// Architecture (Android-safe):
//   - chatView: fixed full-screen overlay, flex column
//   - chatMessages: flex-grow scrollable message list
//   - chatInputBar: always-visible bottom bar with real <input> + send button
//   - The <input> is ALWAYS visible in the DOM — no hidden input tricks.
//     Android keyboard raises naturally when user taps the input field.
//   - viewport-fit=cover + body resize listener scrolls chatMessages to bottom
//     when the software keyboard opens/closes on Android.
//   - Polling: 2s interval while chat open, 4s background when chat closed.
//   - Watermark: server_ts used (not message ts) to avoid clock-drift misses.
//
// Message types: 'system' | 'mine' | 'peer'
// =============================================================================

function openChatWith(peerId, peerName) {
  _chatPeerId   = peerId;
  _chatPeerName = peerName;
  _chatLastTs   = 0;
  delete _connUnread[peerId];

  // Show chat overlay and immediately set correct size for current viewport
  const chatView = $('chatView');
  chatView.style.display = 'flex';
  _applyChatViewport();
  if ($('chatPeerName')) $('chatPeerName').textContent = peerName;

  // Clear messages
  const msgList = $('chatMessages');
  if (msgList) msgList.innerHTML = '';

  _appendMsg('system', '[NOVA SECURE CHANNEL] Connected to ' + peerName);
  _appendMsg('system', '[Messages auto-delete every 50s. Nothing is stored.]');
  _appendMsg('system', '———');

  // Focus the real input — Android keyboard raises automatically
  const inp = $('chatInput');
  if (inp) {
    inp.value = '';
    // Small delay so the overlay is fully rendered before focus
    setTimeout(() => inp.focus(), 80);
  }

  startChatPoll();
}

function closeChatView() {
  stopChatPoll();
  _chatPeerId   = null;
  _chatPeerName = null;

  const cv = $('chatView');
  if (cv) cv.style.display = 'none';

  // Blur input to dismiss keyboard
  const inp = $('chatInput');
  if (inp) { inp.value = ''; inp.blur(); }

  loadConnectionsList();
  startBgPoll();
}

// ── Message rendering ──────────────────────────────────────────────────────

function _appendMsg(type, text, expiresIn) {
  const msgList = $('chatMessages');
  if (!msgList) return;

  const bubble = document.createElement('div');

  if (type === 'system') {
    bubble.className = 'chat-system-msg';
    bubble.textContent = text;
  } else {
    const isMine = (type === 'mine');
    bubble.className = 'chat-bubble ' + (isMine ? 'mine' : 'theirs');

    const inner = document.createElement('div');
    inner.className = 'chat-bubble-inner';

    // Message text
    const textNode = document.createElement('span');
    textNode.className = 'chat-bubble-text';
    textNode.textContent = text;
    inner.appendChild(textNode);

    // Time — inline with text, bottom-right
    const now = new Date();
    let h = now.getHours(), mn = now.getMinutes();
    const ampm = h >= 12 ? 'pm' : 'am';
    h = h % 12 || 12;
    const timeEl = document.createElement('span');
    timeEl.className = 'chat-bubble-time';
    timeEl.textContent = h + ':' + (mn < 10 ? '0' + mn : mn) + ' ' + ampm;
    inner.appendChild(timeEl);

    bubble.appendChild(inner);

    // TTL countdown
    if (expiresIn) {
      const ttl = document.createElement('div');
      ttl.className = 'chat-bubble-ttl';
      ttl.textContent = '~' + Math.round(expiresIn) + 's';
      bubble.appendChild(ttl);

      let remaining = Math.round(expiresIn);
      const ticker = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
          clearInterval(ticker);
          bubble.style.transition = 'opacity 1s ease';
          bubble.style.opacity = '0';
          setTimeout(() => { if (bubble.parentNode) bubble.parentNode.removeChild(bubble); }, 1000);
          return;
        }
        ttl.textContent = '~' + remaining + 's';
        if (remaining <= 10) bubble.style.opacity = String(Math.max(0.15, remaining / 10));
      }, 1000);
    }
  }

  msgList.appendChild(bubble);
  // Scroll to bottom
  msgList.scrollTop = msgList.scrollHeight;
}

// ── Send ───────────────────────────────────────────────────────────────────

let _sendLock    = false;  // true while a send is in-flight
let _lastSentKey = '';     // "<peerId>|<text>|<timestamp bucket>" — dedup key

async function sendChatMessage() {
  const inp = $('chatInput');
  if (!inp || !_chatPeerId) return;
  const text = inp.value.trim();
  if (!text) return;

  // Dedup key: same peer + same text within 600ms window = duplicate event
  const bucket = Math.floor(Date.now() / 600);
  const key = _chatPeerId + '|' + text + '|' + bucket;
  if (_sendLock || key === _lastSentKey) return;

  _sendLock    = true;
  _lastSentKey = key;
  inp.value = '';
  inp.focus(); // keep keyboard open on Android

  try {
    await api('/api/msg/send', 'POST', { peer_id: _chatPeerId, text });
    _appendMsg('mine', text, 50);
    _connLastMsg[_chatPeerId] = { text: 'You: ' + text, ts: Date.now() / 1000 };
  } catch(e) {
    inp.value = text; // restore text so user can retry
    _appendMsg('system', '[ERROR] ' + e.message);
  } finally {
    setTimeout(() => { _sendLock = false; }, 500);
  }
}

// ── Polling ────────────────────────────────────────────────────────────────

function startChatPoll() {
  stopChatPoll();
  stopBgPoll();
  _chatPollTimer = setInterval(pollMessages, 2000);
}

function stopChatPoll() {
  if (_chatPollTimer) { clearInterval(_chatPollTimer); _chatPollTimer = null; }
}

async function pollMessages() {
  if (!_chatPeerId) return;
  try {
    const data = await api('/api/msg/poll?peer_id=' + _chatPeerId + '&since=' + _chatLastTs);
    const msgs = data.messages || [];
    // Use server_ts as watermark — avoids clock-drift causing missed/duplicate messages
    if (data.server_ts) {
      _chatLastTs = data.server_ts;
    } else if (msgs.length) {
      _chatLastTs = Math.max(...msgs.map(m => m.ts));
    }
    for (const m of msgs) {
      if (m.mine) continue; // already shown optimistically on send
      _appendMsg('peer', m.text, m.expires_in);
      _connLastMsg[_chatPeerId] = { text: m.text, ts: m.ts };
    }
  } catch { /* silent — network hiccup is fine */ }
}

// ── Background unread poller (runs when chat is NOT open) ──────────────────

let _bgPollTimer  = null;
let _bgPollLastTs = {}; // { user_id: server_ts }

function startBgPoll() {
  if (_bgPollTimer) return;
  _bgPollTimer = setInterval(bgPollUnread, 4000);
}

function stopBgPoll() {
  if (_bgPollTimer) { clearInterval(_bgPollTimer); _bgPollTimer = null; }
}

async function bgPollUnread() {
  if (_chatPeerId) return; // chat is open — its own poller handles it
  const connList = $('connectList');
  if (!connList) return;
  const rows = connList.querySelectorAll('[data-peer-id]');
  for (const row of rows) {
    const peerId = row.dataset.peerId;
    const since  = _bgPollLastTs[peerId] || 0;
    try {
      const data = await api('/api/msg/poll?peer_id=' + peerId + '&since=' + since);
      const newMsgs = (data.messages || []).filter(m => !m.mine);
      if (newMsgs.length) {
        _connUnread[peerId] = (_connUnread[peerId] || 0) + newMsgs.length;
        const last = newMsgs[newMsgs.length - 1];
        _connLastMsg[peerId] = { text: last.text, ts: last.ts };
        _bgPollLastTs[peerId] = data.server_ts;
        loadConnectionsList();
        break; // one refresh per tick is enough
      }
      if (data.server_ts) _bgPollLastTs[peerId] = data.server_ts;
    } catch { /* ignore */ }
  }
}

// =============================================================================
// PROFILE PHOTO MODAL — URL link only
// =============================================================================

let _profilePhotoUrl     = '';
let _profilePhotoPending = '';

function renderProfilePhotoPreview(url, codeName) {
  const initial = $('profilePhotoInitial');
  const img     = $('profilePhotoImg');
  if (!initial || !img) return;
  if (url) {
    img.src = url;
    img.style.display = '';
    initial.style.display = 'none';
    img.onerror = () => { img.style.display = 'none'; initial.style.display = ''; };
  } else {
    img.style.display = 'none';
    img.src = '';
    initial.style.display = '';
    initial.textContent = (codeName || '?').charAt(0).toUpperCase();
  }
}

function openProfilePhotoModal() {
  $('profilePhotoModal')?.classList.remove('hidden');
  $('ppUrlInput').value    = _profilePhotoUrl;
  _profilePhotoPending     = _profilePhotoUrl;
  if (_profilePhotoUrl) {
    $('ppUrlPreviewImg').src     = _profilePhotoUrl;
    $('ppUrlPreview').style.display = '';
  } else {
    $('ppUrlPreview').style.display = 'none';
  }
  setMsg($('ppMsg'), '');
}

function closeProfilePhotoModal() {
  $('profilePhotoModal')?.classList.add('hidden');
}

function previewProfilePhotoUrl() {
  const url = $('ppUrlInput')?.value.trim();
  const pv  = $('ppUrlPreview');
  const img = $('ppUrlPreviewImg');
  if (url && pv && img) { img.src = url; pv.style.display = ''; _profilePhotoPending = url; }
  else if (pv)            pv.style.display = 'none';
}

function applyProfilePhoto() {
  const final = (_profilePhotoPending || $('ppUrlInput')?.value || '').trim();
  if (!final) { setMsg($('ppMsg'), 'Please enter an image URL'); return; }
  _profilePhotoUrl = final;
  renderProfilePhotoPreview(final, $('profileCodeName')?.textContent || '?');
  closeProfilePhotoModal();
}

$('profilePhotoModal')?.addEventListener('click', e => {
  if (e.target === $('profilePhotoModal')) closeProfilePhotoModal();
});

function _flashShareBtn(msg) {
  const btn = document.getElementById('btnShareQr');
  if (!btn) return;
  const orig = btn.innerHTML;
  btn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> ${msg}`;
  setTimeout(() => { btn.innerHTML = orig; }, 2000);
}

// ── Contact Search Modal ─────────────────────────────────────────
function openContactSearchModal() {
  const modal = document.getElementById('contactSearchModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  renderSearchHistory();
  setTimeout(() => {
    const input = document.getElementById('searchCodeName');
    if (input) input.focus();
  }, 200);
}

function closeContactSearchModal() {
  const modal = document.getElementById('contactSearchModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
}

// Close modal on backdrop click
document.addEventListener('click', (e) => {
  const modal = document.getElementById('contactSearchModal');
  if (modal && !modal.classList.contains('hidden') && e.target === modal) {
    closeContactSearchModal();
  }
});

// Keep backward compat
function toggleSearchPanel() { openContactSearchModal(); }
// ── SCANNER EXTENDED CONTROLS (torch, gallery, code name search) ──────────
function _bindScannerExtras() {
  document.getElementById('btnScannerTorch')?.addEventListener('click', () => toggleTorch());

  document.getElementById('btnScannerGallery')?.addEventListener('click', () => {
    // Stop camera scanning before opening gallery (saves battery, avoids false scans)
    stopCamera();
    const msgEl = document.getElementById('scannerMsg');
    if (msgEl) { msgEl.textContent = 'Select a QR image from gallery...'; msgEl.style.color = 'rgba(255,255,255,0.7)'; }
    document.getElementById('galleryQrInput')?.click();
  });
  document.getElementById('galleryQrInput')?.addEventListener('change', (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) scanFromGallery(file);
    e.target.value = '';
  });

  document.getElementById('btnScannerSearch')?.addEventListener('click', async () => {
    const el = document.getElementById('scannerSearchInput');
    const val = (el ? el.value : '').trim().toUpperCase();
    if (!val) return;
    const msgEl = document.getElementById('scannerMsg');
    if (msgEl) { msgEl.textContent = 'Searching...'; msgEl.style.color = 'rgba(255,255,255,0.7)'; }
    try {
      const res = await api('/api/account/search?code_name=' + encodeURIComponent(val));
      stopCamera();
      closeScannerModal();
      state.receiver = { receiver_code_name: res.code_name, receiver_coin: res.coin, timestamp: null };
      showReceiverBox();
      showPayScreenWithScan();
    } catch (e) {
      if (msgEl) { msgEl.textContent = 'Not found: ' + (e.message || e); msgEl.style.color = 'var(--danger)'; }
    }
  });

  const scanSearchInp = document.getElementById('scannerSearchInput');
  if (scanSearchInp) {
    scanSearchInp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') document.getElementById('btnScannerSearch')?.click();
    });
    scanSearchInp.addEventListener('input', () => {
      scanSearchInp.value = scanSearchInp.value.toUpperCase();
    });
  }
}

// ── SEARCH HISTORY (localStorage key: nova_search_history) ───────────────
const SEARCH_HISTORY_KEY = 'nova_search_history';
const SEARCH_HISTORY_MAX = 10;

function _getSearchHistory() {
  try { return JSON.parse(localStorage.getItem(SEARCH_HISTORY_KEY) || '[]'); } catch { return []; }
}
function _saveSearchHistory(arr) {
  try { localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(arr)); } catch {}
}
function _addToSearchHistory(codeName, coin, coinvalue) {
  let hist = _getSearchHistory();
  // Remove existing entry for same codeName
  hist = hist.filter(h => h.codeName !== codeName);
  // Add to front
  hist.unshift({ codeName, coin, coinvalue, ts: Date.now() });
  // Limit
  if (hist.length > SEARCH_HISTORY_MAX) hist = hist.slice(0, SEARCH_HISTORY_MAX);
  _saveSearchHistory(hist);
  renderSearchHistory();
}
function _clearSearchHistory() {
  _saveSearchHistory([]);
  renderSearchHistory();
}

function renderSearchHistory() {
  const container = document.getElementById('searchHistoryList');
  if (!container) return;
  const hist = _getSearchHistory();
  if (!hist.length) { container.innerHTML = ''; return; }
  const header = '<div class="nova-search-history-header"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> Recent Searches</div>';
  const items = hist.map(h => {
    const relT = _histRelTime(h.ts);
    return `<div class="nova-history-item" onclick="doHistorySearch('${h.codeName}')">
      <div class="nova-history-avatar">${h.codeName.charAt(0)}</div>
      <div class="nova-history-name">${h.codeName}</div>
      <div class="nova-history-time">${relT}</div>
    </div>`;
  }).join('');
  container.innerHTML = header + items;
}

function _histRelTime(ts) {
  if (!ts) return '';
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

async function doHistorySearch(codeName) {
  const inp = document.getElementById('searchCodeName');
  if (inp) inp.value = codeName;
  document.getElementById('btnSearch')?.click();
}

// Override btnSearch click to show Android contact card + save history
function _initContactSearch() {
  const btn = document.getElementById('btnSearch');
  if (!btn) return;
  // Remove old listeners by cloning
  const newBtn = btn.cloneNode(true);
  btn.parentNode.replaceChild(newBtn, btn);

  newBtn.addEventListener('click', async () => {
    const code = normalizeCode(document.getElementById('searchCodeName')?.value);
    const resultBox = document.getElementById('searchResult');
    if (!resultBox) return;
    resultBox.classList.add('hidden');
    resultBox.innerHTML = '';
    try {
      if (!code) throw new Error('Enter code name');
      const res = await api('/api/account/search?code_name=' + encodeURIComponent(code));
      const rupees = (res.coinvalue * 10000).toLocaleString('en-IN');
      const letter = (res.code_name || '?').charAt(0).toUpperCase();

      const photoUrl = res.photo_url || res.profile_photo || res.profile_pic || '';
      const avatarInner = photoUrl
        ? `<img src="${photoUrl}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:50%;" onerror="this.style.display='none';this.nextElementSibling.style.display='block';" /><span style="display:none;font-size:32px;font-weight:700;color:#000;">${letter}</span>`
        : `<span style="font-size:32px;font-weight:700;color:#000;">${letter}</span>`;

      const bio = res.bio || '';
      const jobTitle = res.work_title || res.job_title || '';
      const company = res.work_company || res.company || '';
      const location = res.work_location || res.location || '';
      const infoRows = [
        bio      ? { icon: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="rgba(212,175,55,0.8)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>', label: 'About', value: bio } : null,
        jobTitle ? { icon: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="rgba(212,175,55,0.8)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>', label: 'Job Title', value: jobTitle } : null,
        company  ? { icon: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="rgba(212,175,55,0.8)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>', label: 'Company', value: company } : null,
        location ? { icon: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="rgba(212,175,55,0.8)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>', label: 'Location', value: location } : null,
      ].filter(Boolean);
      const profileInfoHtml = infoRows.length ? `<div class="nc-section"><div class="nc-section-label">ABOUT</div>${infoRows.map(r => `<div class="nc-detail-row"><div class="nc-detail-icon">${r.icon}</div><div><div class="nc-detail-label">${r.label}</div><div class="nc-detail-value">${r.value}</div></div></div>`).join('')}</div>` : '';

      const myCodeName = state.me ? state.me.code_name : '';
      const isSelf = res.code_name === myCodeName;
      const sendBtnHtml = isSelf ? '' : `
        <div class="nc-actions">
          <button class="nc-action-btn" onclick="closeContactSearchModal();state.receiver={receiver_code_name:'${res.code_name}',receiver_coin:'${res.coin}',timestamp:null};showReceiverBox();showPayScreen();">
            <div class="nc-action-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg></div>
            <span>Send Coins</span>
          </button>
        </div>`;

      resultBox.classList.remove('hidden');
      resultBox.innerHTML = `
        <div class="nc-card">
          <div class="nc-hero">
            <div class="nc-avatar">${avatarInner}</div>
            <div class="nc-hero-name">${res.code_name}</div>
            <div class="nc-hero-sub">Nova Member</div>
          </div>
          ${sendBtnHtml}
          <div class="nc-section">
            <div class="nc-section-label">NOVA ACCOUNT</div>
            <div class="nc-detail-row">
              <div class="nc-detail-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="rgba(212,175,55,0.8)" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg></div>
              <div><div class="nc-detail-label">Account No</div><div class="nc-detail-value" style="font-family:monospace;font-size:12px;">${res.coin}</div></div>
            </div>
            <div class="nc-detail-row">
              <div class="nc-detail-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="rgba(212,175,55,0.8)" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg></div>
              <div><div class="nc-detail-label">Balance</div><div class="nc-detail-value" style="color:var(--gold2);">${res.coinvalue} Coin${res.coinvalue !== 1 ? 's' : ''} <span style="color:rgba(255,255,255,0.35);font-size:11px;">(₹${rupees})</span></div></div>
            </div>
          </div>
          ${profileInfoHtml}
          <div class="nc-section">
            <div class="nc-section-label">TRANSACTION HISTORY</div>
            <div id="contactTxList_${res.code_name}" class="nc-tx-list">
              <div class="nc-tx-loading">Loading…</div>
            </div>
          </div>
        </div>
      `;

      _loadContactTransactions(res.code_name);
      _addToSearchHistory(res.code_name, res.coin, res.coinvalue);
    } catch (e) {
      resultBox.classList.remove('hidden');
      resultBox.innerHTML = `<div style="padding:20px;color:var(--danger);font-size:13px;text-align:center;">${e.message || e}</div>`;
    }
  });
}

// Clear search history btn
function _bindClearHistory() {
  document.getElementById('btnClearSearchHistory')?.addEventListener('click', () => {
    _clearSearchHistory();
  });
}

// Load transactions between current user and searched contact
async function _loadContactTransactions(codeName) {
  const listEl = document.getElementById('contactTxList_' + codeName);
  if (!listEl) return;
  try {
    const txRes = await api('/api/transactions');
    const txs = (txRes.transactions || []).filter(t =>
      t.payer_code_name === codeName || t.receiver_code_name === codeName
    );
    if (!txs.length) {
      listEl.innerHTML = '<div class="nova-contact-tx-empty">No transactions with this user yet.</div>';
      return;
    }
    const myCode = state.me ? state.me.code_name : '';
    listEl.innerHTML = txs.slice(0, 8).map(t => {
      const isSent = t.payer_code_name === myCode;
      const rupeeAmount = (t.amount * 10000).toLocaleString('en-IN');
      const dirIcon = isSent
        ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/></svg>`
        : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--ok)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="17" y1="7" x2="7" y2="17"/><polyline points="17 17 7 17 7 7"/></svg>`;
      const label = isSent ? 'Sent' : 'Received';
      const color = isSent ? 'var(--danger)' : 'var(--ok)';
      return `<div class="nova-contact-tx-item">
        <div class="nova-contact-tx-dir">${dirIcon}</div>
        <div class="nova-contact-tx-info">
          <div class="nova-contact-tx-label" style="color:${color};">${label} ${t.amount} Coin${t.amount !== 1 ? 's' : ''}</div>
          <div class="nova-contact-tx-time">${t.created_at || ''}</div>
        </div>
        <div class="nova-contact-tx-amount" style="color:${color};">₹${rupeeAmount}</div>
      </div>`;
    }).join('');
  } catch (e) {
    if (listEl) listEl.innerHTML = '<div class="nova-contact-tx-empty">Could not load history.</div>';
  }
}

// Show pay screen helper (for send coins button)
function showPayScreen() {
  showScreen('pay');
}

// Patch boot to include these
const _origStoreBoot = boot;
boot = function() {
  _origStoreBoot();
  setTimeout(() => {
    _bindScannerExtras();
    _initContactSearch();
    _bindClearHistory();
    renderSearchHistory();
  }, 100);
};
// ═══════════════════════════════════════════════════════════════════════
// TRANSACTION HISTORY MODAL (Search History button beside My Orders)
// ═══════════════════════════════════════════════════════════════════════

function openTxHistoryModal() {
  const modal = document.getElementById('txHistoryModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');

  // Default dates: past 30 days to today
  const today = new Date();
  const past30 = new Date(today);
  past30.setDate(today.getDate() - 30);

  const toInput   = document.getElementById('txHistToDate');
  const fromInput = document.getElementById('txHistFromDate');
  if (toInput && !toInput.value)   toInput.value   = today.toISOString().split('T')[0];
  if (fromInput && !fromInput.value) fromInput.value = past30.toISOString().split('T')[0];

  loadTxHistory();
}

function closeTxHistoryModal() {
  const modal = document.getElementById('txHistoryModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
}

document.addEventListener('click', (e) => {
  const modal = document.getElementById('txHistoryModal');
  if (modal && !modal.classList.contains('hidden') && e.target === modal) closeTxHistoryModal();
});

async function loadTxHistory() {
  const listEl  = document.getElementById('txHistoryList');
  const msgEl   = document.getElementById('txHistMsg');
  const fromVal = document.getElementById('txHistFromDate')?.value;
  const toVal   = document.getElementById('txHistToDate')?.value;

  if (!listEl) return;
  listEl.innerHTML = '<div class="muted small" style="padding:12px 0;">Loading...</div>';
  if (msgEl) msgEl.textContent = '';

  try {
    // Validate dates
    const now = new Date();
    now.setHours(23,59,59,999);
    let fromDate = fromVal ? new Date(fromVal) : null;
    let toDate   = toVal   ? new Date(toVal)   : null;

    // Future query guard: cap toDate at today
    if (toDate && toDate > now) {
      toDate = now;
      const todayStr = now.toISOString().split('T')[0];
      const toInput = document.getElementById('txHistToDate');
      if (toInput) toInput.value = todayStr;
      if (msgEl) { msgEl.textContent = 'Future dates adjusted to today.'; }
    }

    const res = await api('/api/transactions');
    let txs = res.transactions || [];

    // Filter by date range
    if (fromDate || toDate) {
      txs = txs.filter(t => {
        if (!t.created_at) return true;
        const d = new Date(t.created_at);
        if (fromDate && d < fromDate) return false;
        if (toDate) {
          const tEnd = new Date(toDate);
          tEnd.setHours(23,59,59,999);
          if (d > tEnd) return false;
        }
        return true;
      });
    }

    if (!txs.length) {
      listEl.innerHTML = '<div class="muted small" style="padding:16px 0;text-align:center;">No transactions found in this date range.</div>';
      return;
    }

    const myCode = state.me ? state.me.code_name : '';
    listEl.innerHTML = txs.map(t => {
      const isSent    = t.payer_code_name === myCode;
      const color     = isSent ? 'var(--danger)' : 'var(--ok)';
      const label     = isSent ? 'Sent to' : 'Received from';
      const peer      = isSent ? (t.receiver_code_name || '-') : (t.payer_code_name || '-');
      const rupees    = ((t.amount || 0) * 10000).toLocaleString('en-IN');
      const dirSvg    = isSent
        ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/></svg>`
        : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="17" y1="7" x2="7" y2="17"/><polyline points="17 17 7 17 7 7"/></svg>`;
      return `<div class="tx-hist-item">
        <div class="tx-hist-dir-icon">${dirSvg}</div>
        <div class="tx-hist-info">
          <div class="tx-hist-peer">${label} <strong style="color:#e0e0e0;">${peer}</strong></div>
          <div class="tx-hist-date">${t.created_at || ''}</div>
        </div>
        <div class="tx-hist-amount">
          <div style="color:${color};">${isSent ? '-' : '+'}${t.amount} Coin${t.amount !== 1 ? 's' : ''}</div>
          <div class="tx-hist-rupee">₹${rupees}</div>
        </div>
      </div>`;
    }).join('');

  } catch(e) {
    if (listEl) listEl.innerHTML = `<div style="color:var(--danger);font-size:13px;padding:12px 0;">${e.message || 'Failed to load'}</div>`;
  }
}

// ═══════════════════════════════════════════════════════════════════════
// NOTIFICATIONS SYSTEM (DB-backed broadcast messages — delivered to ALL users)
// ═══════════════════════════════════════════════════════════════════════

const NOTIF_READ_KEY = 'nova_broadcasts_read';

// DB broadcasts cached in memory for this session
let _dbBroadcasts = [];

async function _fetchBroadcasts() {
  try {
    const data = await api('/api/broadcasts');
    if (data && data.ok && Array.isArray(data.broadcasts)) {
      _dbBroadcasts = data.broadcasts;
      _updateNotifBadge();
    }
  } catch(e) {
    // silently fail — user not logged in or network error
  }
}

function _getReadIds() {
  try { return JSON.parse(localStorage.getItem(NOTIF_READ_KEY) || '[]'); } catch { return []; }
}
function _markAllRead() {
  try { localStorage.setItem(NOTIF_READ_KEY, JSON.stringify(_dbBroadcasts.map(n => n.id))); } catch {}
  _updateNotifBadge();
}

function _updateNotifBadge() {
  const badge   = document.getElementById('notifBadge');
  if (!badge) return;
  const readIds = _getReadIds();
  const unread  = _dbBroadcasts.filter(n => !readIds.includes(n.id)).length;
  if (unread > 0) {
    badge.textContent = unread > 99 ? '99+' : String(unread);
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}

function openNotifModal() {
  const modal = document.getElementById('notifModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  _fetchBroadcasts().then(() => _renderNotifList());
  _markAllRead();
}

function closeNotifModal() {
  const modal = document.getElementById('notifModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
}

document.addEventListener('click', (e) => {
  const modal = document.getElementById('notifModal');
  if (modal && !modal.classList.contains('hidden') && e.target === modal) closeNotifModal();
});

function _renderNotifList() {
  const listEl  = document.getElementById('notifList');
  if (!listEl) return;
  const readIds = _getReadIds();
  if (!_dbBroadcasts.length) {
    listEl.innerHTML = '<div class="muted small" style="padding:20px;text-align:center;">No notifications yet.</div>';
    return;
  }
  listEl.innerHTML = _dbBroadcasts.map(n => {
    const isUnread = !readIds.includes(n.id);
    const relT = _notifRelTime(n.ts);
    const delBtn = isStoreAdmin()
      ? `<button class="nova-notif-item-del" onclick="deleteNotif('${n.id}')" title="Delete">&#x2715;</button>`
      : '';
    return `<div class="nova-notif-item ${isUnread ? 'unread' : ''}" id="notif_${n.id}">
      ${delBtn}
      <div class="nova-notif-item-title">${_escHtml(n.title || 'Notice')}</div>
      <div class="nova-notif-item-body">${_escHtml(n.body || '')}</div>
      <div class="nova-notif-item-time">${relT}</div>
    </div>`;
  }).join('');
}

function _escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _notifRelTime(ts) {
  if (!ts) return '';
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60)    return 'Just now';
  if (diff < 3600)  return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

async function deleteNotif(id) {
  if (!isStoreAdmin()) return;
  try {
    await api(`/api/admin/broadcast/${id}`, 'DELETE');
    _dbBroadcasts = _dbBroadcasts.filter(n => n.id !== id);
    _updateNotifBadge();
    _renderNotifList();
    const adminBroadcastList = document.getElementById('adminBroadcastList');
    if (adminBroadcastList) loadAdminBroadcasts();
  } catch(e) {
    console.error('Failed to delete broadcast:', e);
  }
}

// ═══════════════════════════════════════════════════════════════════════
// ADMIN — BROADCAST MANAGEMENT
// ═══════════════════════════════════════════════════════════════════════

async function adminSendBroadcast() {
  if (!isStoreAdmin()) return;
  const titleEl = document.getElementById('broadcastTitle');
  const bodyEl  = document.getElementById('broadcastBody');
  const msgEl   = document.getElementById('broadcastMsg');
  const btnEl   = document.getElementById('btnAdminBroadcast');
  const title   = (titleEl?.value || '').trim();
  const body    = (bodyEl?.value  || '').trim();
  if (!title)  { if (msgEl) { msgEl.textContent = 'Please enter a title.'; msgEl.style.color = 'var(--danger)'; } return; }
  if (!body)   { if (msgEl) { msgEl.textContent = 'Please enter a message.'; msgEl.style.color = 'var(--danger)'; } return; }

  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Sending…'; }
  if (msgEl) { msgEl.textContent = ''; }

  try {
    const res = await api('/api/admin/broadcast', 'POST', { title, body, send_email: true });
    if (res.ok) {
      if (titleEl) titleEl.value = '';
      if (bodyEl)  bodyEl.value  = '';
      if (msgEl) {
        msgEl.textContent = `✓ Broadcast sent! Emails queued for ${res.email_queued ?? 0} user(s).`;
        msgEl.style.color = 'var(--ok)';
        setTimeout(() => { if (msgEl) msgEl.textContent = ''; }, 4000);
      }
      await _fetchBroadcasts();
      loadAdminBroadcasts();
    }
  } catch(e) {
    if (msgEl) { msgEl.textContent = e.message || 'Broadcast failed.'; msgEl.style.color = 'var(--danger)'; }
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Send Broadcast'; }
  }
}

async function loadAdminBroadcasts() {
  const listEl = document.getElementById('adminBroadcastList');
  if (!listEl) return;
  await _fetchBroadcasts();
  const notifs = _dbBroadcasts;
  if (!notifs.length) {
    listEl.innerHTML = '<div class="muted small" style="padding:8px 0;">No broadcasts sent yet.</div>';
    return;
  }
  listEl.innerHTML = notifs.map(n => {
    const relT = _notifRelTime(n.ts);
    return `<div class="admin-broadcast-item">
      <button class="admin-broadcast-del" onclick="deleteNotif('${n.id}')" title="Delete message">&#x2715;</button>
      <div class="admin-broadcast-title">${_escHtml(n.title)}</div>
      <div class="admin-broadcast-body">${_escHtml(n.body)}</div>
      <div class="admin-broadcast-meta">${relT} · by ${_escHtml(n.by || 'Admin')}</div>
    </div>`;
  }).join('');
}

// Init notification badge on boot
const _origBootForNotif = boot;
boot = function() {
  _origBootForNotif();
  setTimeout(() => {
    _fetchBroadcasts();
    // Poll every 60s for new broadcasts from DB
    setInterval(_fetchBroadcasts, 60000);
  }, 300);
};

// ═══════════════════════════════════════════════════════════════════════
// COIN → CASH CONVERSION MODAL
// ═══════════════════════════════════════════════════════════════════════

function openCoinToCashModal() {
  if (!state.me) { showAuthRequiredToast('coin-to-cash'); return; }
  const modal = document.getElementById('coinToCashModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');

  // Populate balance
  const balEl = document.getElementById('c2cBalanceDisplay');
  if (balEl) balEl.textContent = `${state.me.coinvalue ?? 0} Coin${state.me.coinvalue !== 1 ? 's' : ''}`;

  // Reset form
  const fields = ['c2cCoins','c2cUpiId','c2cAccHolder','c2cBankName','c2cAccNo','c2cIfsc'];
  fields.forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  const agree = document.getElementById('c2cAgree');
  if (agree) agree.checked = false;
  const preview = document.getElementById('c2cPreview');
  if (preview) preview.classList.add('hidden');
  const msgEl = document.getElementById('c2cMsg');
  if (msgEl) { msgEl.style.display = 'none'; msgEl.textContent = ''; }

  // Default to UPI
  c2cSwitchMethod('upi');
}

function closeCoinToCashModal() {
  const modal = document.getElementById('coinToCashModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
}

document.addEventListener('click', (e) => {
  const modal = document.getElementById('coinToCashModal');
  if (modal && !modal.classList.contains('hidden') && e.target === modal) closeCoinToCashModal();
});

function c2cSwitchMethod(method) {
  document.getElementById('c2cUpiSection').style.display  = method === 'upi'  ? '' : 'none';
  document.getElementById('c2cBankSection').style.display = method === 'bank' ? '' : 'none';
  document.getElementById('c2cBtnUpi').classList.toggle('active',  method === 'upi');
  document.getElementById('c2cBtnBank').classList.toggle('active', method === 'bank');
}

function c2cCalcPreview() {
  const coinsVal = parseFloat(document.getElementById('c2cCoins')?.value || '0');
  const preview  = document.getElementById('c2cPreview');
  if (!preview) return;
  if (!coinsVal || coinsVal <= 0) { preview.classList.add('hidden'); return; }

  const gross     = coinsVal * 10000;
  const charge    = gross * 0.10;
  const net       = gross - charge;
  const fmt       = v => '₹' + v.toLocaleString('en-IN');

  const grossEl   = document.getElementById('c2cGross');
  const chargeEl  = document.getElementById('c2cCharge');
  const netEl     = document.getElementById('c2cNet');
  if (grossEl)  grossEl.textContent  = fmt(gross);
  if (chargeEl) chargeEl.textContent = '-' + fmt(charge);
  if (netEl)    netEl.textContent    = fmt(net);

  preview.classList.remove('hidden');
}

async function submitCoinToCash() {
  const msgEl  = document.getElementById('c2cMsg');
  const btnEl  = document.getElementById('btnC2CSubmit');
  const setMsg = (txt, ok) => {
    if (!msgEl) return;
    msgEl.textContent  = txt;
    msgEl.style.color  = ok ? 'var(--ok)' : 'var(--danger)';
    msgEl.style.display = 'block';
  };

  const coins  = parseFloat(document.getElementById('c2cCoins')?.value || '0');
  const agreed = document.getElementById('c2cAgree')?.checked;
  const isUpi  = document.getElementById('c2cBtnUpi')?.classList.contains('active');

  if (!coins || coins <= 0) { setMsg('Enter a valid coin amount.', false); return; }
  if (!agreed) { setMsg('Please agree to the 10% deduction to proceed.', false); return; }
  if (state.me && coins > state.me.coinvalue) {
    setMsg(`Insufficient balance. You have ${state.me.coinvalue} coins.`, false); return;
  }

  const payload = {
    coins,
    agreed_10pct: true,
    upi_id:         isUpi  ? (document.getElementById('c2cUpiId')?.value || '').trim()  : '',
    bank_name:     !isUpi  ? (document.getElementById('c2cBankName')?.value || '').trim() : '',
    account_no:    !isUpi  ? (document.getElementById('c2cAccNo')?.value || '').trim()   : '',
    ifsc:          !isUpi  ? (document.getElementById('c2cIfsc')?.value  || '').trim().toUpperCase() : '',
    account_holder:!isUpi  ? (document.getElementById('c2cAccHolder')?.value || '').trim() : '',
  };

  if (isUpi && !payload.upi_id)      { setMsg('Enter your UPI ID.', false); return; }
  if (!isUpi && !payload.account_no) { setMsg('Enter account number.', false); return; }
  if (!isUpi && !payload.ifsc)       { setMsg('Enter IFSC code.', false); return; }

  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Submitting…'; }
  if (msgEl) msgEl.style.display = 'none';

  try {
    const res = await api('/api/coin-to-cash-request', 'POST', payload);
    if (res.ok) {
      setMsg(res.message || `Request submitted! ₹${Number(res.net_inr).toLocaleString('en-IN')} will be transferred within 2–3 working days.`, true);
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Submit Conversion Request'; }
      // Disable submit after success
      if (btnEl) { btnEl.disabled = true; btnEl.style.opacity = '0.5'; }
    }
  } catch(e) {
    setMsg(e.message || 'Submission failed. Please try again.', false);
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Submit Conversion Request'; }
  }
}