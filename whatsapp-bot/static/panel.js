let CONVS = [];
let CURRENT_PHONE = null;
let CURRENT_INFO = null;
let CURRENT_PARTNER = null;
let POLL_TIMER = null;
let CURRENT_STATUS_FILTER = '';  // '' = todos
let CURRENT_QUICK_FILTER = '';   // '' | 'unread' | 'escalated' | 'today'

const STATUS_ORDER = ['nuevo','en_conversacion','cotizado','pagado','a_despachar','cerrado'];
const STATUS_LABELS = {
  nuevo:'🆕 Nuevos', en_conversacion:'💬 En conv.', cotizado:'📋 Cotizado',
  pagado:'💰 Pagado', a_despachar:'📦 A despachar', cerrado:'✅ Cerrado'
};
const STATUS_LABELS_FULL = {
  nuevo:'NUEVO', en_conversacion:'EN CONV.', cotizado:'COTIZADO',
  pagado:'PAGADO', a_despachar:'A DESPACHAR', cerrado:'CERRADO'
};

async function api(path, opts){
  const r = await fetch(path, {credentials:'same-origin', ...(opts||{})});
  if(r.status === 401){ location.reload(); return null; }
  if(!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

function fmtTime(ts){
  if(!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if(sameDay) return d.toLocaleTimeString('es-CR',{hour:'2-digit',minute:'2-digit'});
  const diffDays = Math.floor((now-d)/86400000);
  if(diffDays < 7) return d.toLocaleDateString('es-CR',{weekday:'short'});
  return d.toLocaleDateString('es-CR',{day:'2-digit',month:'2-digit'});
}

function initials(name, phone){
  if(name && name.trim()){
    const parts = name.trim().split(/\s+/);
    const a = (parts[0] && parts[0][0]) ? parts[0][0] : '';
    const b = (parts[1] && parts[1][0]) ? parts[1][0] : '';
    return ((a + b).toUpperCase()) || (phone || '?').slice(-2);
  }
  return (phone || '?').slice(-2);
}

async function loadStats(){
  try{
    const s = await api('/api/stats');
    if(!s) return;
    const f = CURRENT_QUICK_FILTER;
    document.getElementById('stats').innerHTML = `
      <div class="stat ${f===''?'active':''}" onclick="setQuickFilter('')" title="Mostrar todas las conversaciones"><div class="stat-label">Total</div><div class="stat-val">${s.total}</div></div>
      <div class="stat ${f==='unread'?'active':''}" onclick="setQuickFilter('unread')" title="Solo conversaciones sin leer"><div class="stat-label">Sin leer</div><div class="stat-val yellow">${s.unread}</div></div>
      <div class="stat ${f==='escalated'?'active':''}" onclick="setQuickFilter('escalated')" title="Solo conversaciones escaladas a humano"><div class="stat-label">Escaladas</div><div class="stat-val red">${s.escalated}</div></div>
      <div class="stat ${f==='today'?'active':''}" onclick="setQuickFilter('today')" title="Solo conversaciones con actividad hoy"><div class="stat-label">Hoy</div><div class="stat-val blue">${s.msgs_today}</div></div>
    `;
    const hb = document.getElementById('hoursBadge');
    // Solo el círculo + texto compacto. El CSS @media oculta el .badge-text en topbar estrecho.
    if(s.business_hours){ hb.innerHTML = '<span class="badge-text">En horario</span>'; hb.className = 'badge live'; }
    else { hb.innerHTML = '<span class="badge-text">Fuera horario</span>'; hb.className = 'badge off'; }
    // Render tabs estado
    const totalAll = STATUS_ORDER.reduce((a,k)=>a+(s.by_status?.[k]||0),0);
    const tabsEl = document.getElementById('statusTabs');
    tabsEl.innerHTML = `<button class="status-tab ${CURRENT_STATUS_FILTER===''?'active':''}" data-status="" onclick="setStatusFilter('')">Todos <span class="cnt">${totalAll}</span></button>`
      + STATUS_ORDER.map(k => `<button class="status-tab ${CURRENT_STATUS_FILTER===k?'active':''}" data-status="${k}" onclick="setStatusFilter('${k}')">${STATUS_LABELS[k]} <span class="cnt">${s.by_status?.[k]||0}</span></button>`).join('');
  }catch(e){ console.error(e); }
}

let ALL_CONVS = []; // todas las conversaciones (sin filtro) — alimenta la píldora móvil

async function loadConvs(){
  try{
    const qs = CURRENT_STATUS_FILTER ? ('?status='+encodeURIComponent(CURRENT_STATUS_FILTER)) : '';
    CONVS = await api('/api/conversations'+qs) || [];
    renderConvs();
    // ALL_CONVS solo si hay filtro activo (si no, reutilizamos CONVS)
    if(CURRENT_STATUS_FILTER){
      try{ ALL_CONVS = await api('/api/conversations') || []; }
      catch(_){ ALL_CONVS = CONVS; }
    }else{
      ALL_CONVS = CONVS;
    }
    updateMobilePill();
  }catch(e){ console.error(e); }
}

// Píldora flotante móvil — visible solo dentro de un chat. Cuenta unread y conversaciones pendientes de despacho.
function updateMobilePill(){
  const el = document.getElementById('mobilePill');
  if(!el) return;
  let unread = 0, pendingShip = 0, escalated = 0, otherUnreadName = null;
  for(const c of (ALL_CONVS || [])){
    if(c.unread && c.phone !== CURRENT_PHONE){
      unread++;
      if(!otherUnreadName) otherUnreadName = c.name || c.phone;
    }
    if(c.status === 'pagado' || c.status === 'a_despachar') pendingShip++;
    if(c.escalated) escalated++;
  }
  const parts = [];
  if(unread > 0){
    const tail = (unread===1 && otherUnreadName) ? (' · ' + String(otherUnreadName).split(' ')[0]) : '';
    parts.push(`<span class="pill-dot"></span>${unread} sin leer${tail}`);
  }
  if(pendingShip > 0){
    parts.push(`<span>📦 ${pendingShip} por despachar</span>`);
  }
  if(escalated > 0){
    parts.push(`<span>⚠️ ${escalated} escaladas</span>`);
  }
  if(parts.length === 0){
    el.classList.remove('has-content');
    el.innerHTML = '';
    return;
  }
  el.classList.add('has-content');
  el.innerHTML = parts.join('<span class="pill-sep">·</span>');
}

function setStatusFilter(s){
  CURRENT_STATUS_FILTER = s;
  // Repintar tabs activas (loadStats lo hace, pero también acá para feedback inmediato)
  document.querySelectorAll('.status-tab').forEach(b => b.classList.toggle('active', (b.dataset.status||'') === s));
  loadConvs();
}

function setQuickFilter(name){
  // Toggle: si tocas el mismo, lo apagás
  if(CURRENT_QUICK_FILTER === name) name = '';
  CURRENT_QUICK_FILTER = name;
  // Repintar inmediatamente
  document.querySelectorAll('.stats .stat').forEach((el, idx) => {
    const map = ['', 'unread', 'escalated', 'today'];
    el.classList.toggle('active', map[idx] === name);
  });
  renderConvs();
}

function _matchQuickFilter(c){
  switch(CURRENT_QUICK_FILTER){
    case 'unread':    return (c.unread || 0) > 0;
    case 'escalated': return !!c.escalated;
    case 'today': {
      if(!c.last_seen) return false;
      const d = new Date(c.last_seen * 1000);
      const now = new Date();
      return d.toDateString() === now.toDateString();
    }
    default: return true;
  }
}

function renderConvs(){
  const q = (document.getElementById('searchInput').value || '').toLowerCase().trim();
  // Quick filter primero (sobre todas las CONVS), luego search por texto
  const base = (CURRENT_QUICK_FILTER ? CONVS.filter(_matchQuickFilter) : CONVS);
  const filtered = q
    ? base.filter(c => (c.name||'').toLowerCase().includes(q) || (c.phone||'').includes(q))
    : base;
  const list = document.getElementById('convList');
  if(!filtered.length){
    const emptyMsg = CURRENT_QUICK_FILTER
      ? `Sin conversaciones para el filtro <strong>${CURRENT_QUICK_FILTER==='unread'?'Sin leer':CURRENT_QUICK_FILTER==='escalated'?'Escaladas':'Hoy'}</strong><br><button class="status-tab" style="margin-top:10px" onclick="setQuickFilter('')">Quitar filtro</button>`
      : 'Sin conversaciones';
    list.innerHTML = `<div style="padding:30px;text-align:center;color:var(--text3);font-size:.78rem">${emptyMsg}</div>`;
    return;
  }
  list.innerHTML = filtered.map(c => {
    const st = c.status || 'nuevo';
    return `
    <div class="conv ${c.phone === CURRENT_PHONE ? 'active' : ''} ${c.unread>0?'has-unread':''}" onclick="openConv('${c.phone}')">
      <div class="conv-avatar">${initials(c.name, c.phone)}</div>
      <div class="conv-info">
        <div class="conv-name">${escapeHtml(c.name || '+' + c.phone)}</div>
        <div style="display:flex; gap:5px; align-items:center; margin-top:2px">
          <span class="status-badge sb-${st}">${STATUS_LABELS_FULL[st] || st}</span>
          ${c.odoo_sale_order_name ? `<span style="font-size:.55rem; color:var(--text3); font-family:monospace">${c.odoo_sale_order_name}</span>` : ''}
        </div>
        <div class="conv-prev" style="margin-top:3px">${escapeHtml(c.last_message_preview || '')}</div>
      </div>
      <div class="conv-meta">
        <div class="conv-time">${fmtTime(c.last_seen)}</div>
        ${c.unread > 0 ? `<div class="conv-unread">${c.unread}</div>` : ''}
        ${c.escalated ? '<span class="esc-icon" title="Escalada">⚠</span>' : ''}
      </div>
    </div>`;
  }).join('');
}

async function openConv(phone){
  const isSwitching = (CURRENT_PHONE !== phone);
  CURRENT_PHONE = phone;
  if(isSwitching) LAST_MSGS_SIG = '';
  document.getElementById('app').classList.add('show-chat');
  document.getElementById('empty').style.display = 'none';
  document.getElementById('chatHead').style.display = 'flex';
  document.getElementById('chatBody').style.display = '';
  document.getElementById('chatFoot').style.display = 'flex';
  renderConvs();
  updateMobilePill();
  try{
    const d = await api('/api/conversation/' + encodeURIComponent(phone));
    if(!d) return;
    CURRENT_INFO = d.info;
    CURRENT_PARTNER = d.partner;
    document.getElementById('chatName').textContent = d.info?.name || '+' + phone;
    document.getElementById('chatPhone').textContent = '+' + phone;
    renderStatusBadge();
    renderActions();
    renderPartner(d.partner);
    renderMessages(d.messages || [], {force: isSwitching});
    // refresca conteos
    loadConvs();
    loadStats();
  }catch(e){ console.error(e); }
}

function renderStatusBadge(){
  const el = document.getElementById('chatStatusBadge');
  const st = CURRENT_INFO?.status || 'nuevo';
  el.className = 'status-badge sb-' + st;
  el.textContent = STATUS_LABELS_FULL[st] || st;
}

// Menú flotante para cambiar el estado manualmente (botón badge del chat header).
// Útil cuando el flujo automático no llegó al estado real — p.ej. el cliente llamó por
// teléfono y la conversación quedó colgada en "en_conversacion" pero ya está cerrada.
const STATUS_COLORS = {
  nuevo:'#fbc02d', en_conversacion:'#8696a0', cotizado:'#3b6cb5',
  pagado:'#008069', a_despachar:'#a855f7', cerrado:'#54656f'
};
function openStatusMenu(ev){
  if(ev){ ev.stopPropagation(); ev.preventDefault(); }
  const menu = document.getElementById('statusMenu');
  const badge = document.getElementById('chatStatusBadge');
  if(!menu || !badge) return;
  if(menu.classList.contains('open')){
    menu.classList.remove('open');
    return;
  }
  const cur = CURRENT_INFO?.status || 'nuevo';
  menu.innerHTML = STATUS_ORDER.map(s => {
    const isCur = (s === cur);
    return `<div class="opt${isCur?' current':''}" ${isCur?'':`onclick="pickStatus('${s}')"`}>
      <span class="dot" style="background:${STATUS_COLORS[s]||'#999'}"></span>
      <span>${STATUS_LABELS_FULL[s] || s}</span>
      ${isCur?'<span class="check">● actual</span>':''}
    </div>`;
  }).join('');
  // Posicionamos el menú justo debajo del badge.
  const rect = badge.getBoundingClientRect();
  menu.style.top = (rect.bottom + window.scrollY + 6) + 'px';
  menu.style.left = (rect.left + window.scrollX) + 'px';
  menu.classList.add('open');
}
function closeStatusMenu(){
  document.getElementById('statusMenu')?.classList.remove('open');
}
async function pickStatus(s){
  closeStatusMenu();
  if(!s || s === CURRENT_INFO?.status) return;
  await markStatus(s);
}
document.addEventListener('click', (e) => {
  const m = document.getElementById('statusMenu');
  if(!m || !m.classList.contains('open')) return;
  if(!m.contains(e.target) && !e.target.closest('#chatStatusBadge')){
    m.classList.remove('open');
  }
});

function renderActions(){
  renderActionBanner();
  // NO re-renderizar el drawer si está abierto — el polling cada 8s borra lo que el usuario
  // está editando o seleccionando (calculadora envío, datos editables, etc.).
  // El drawer se actualiza solo cuando el usuario lo cierra y vuelve a abrirlo.
}

function renderActionBanner(){
  const banner = document.getElementById('actionBanner');
  if(!banner) return;
  if(!CURRENT_INFO){ banner.classList.remove('show'); banner.innerHTML = ''; return; }
  const st = CURRENT_INFO.status || 'nuevo';
  const name = escapeHtml(CURRENT_INFO.name || ('+' + CURRENT_PHONE));
  const orderName = CURRENT_INFO.odoo_sale_order_name || '';
  const orderId = CURRENT_INFO.odoo_sale_order_id;
  const payment = CURRENT_INFO.payment_meta_parsed || null;
  const escalated = !!CURRENT_INFO.escalated;
  const escBtn = `<button class="banner-btn ${escalated?'warning':''}" onclick="toggleEscalate()">${escalated?'⚠ Bot desactivado (volver a activar)':'👤 Tomar conversación (desactivar bot)'}</button>`;

  const collapsed = localStorage.getItem('bannerCollapsed') === '1';
  banner.className = 'action-banner show banner-' + st + (collapsed ? ' collapsed' : '');
  let html = '';

  if(st === 'nuevo'){
    html = `
      <div class="banner-header">🆕 Cliente nuevo · ${name}</div>
      <div class="banner-sub">Primera conversación. El bot va a responder automáticamente cuando el cliente escriba.</div>
      <div class="banner-actions">
        ${escBtn}
        <button class="banner-btn prim" onclick="openManualQuoteModal()">📋 Crear cotización manual para ${name}</button>
      </div>`;
  } else if(st === 'en_conversacion'){
    html = `
      <div class="banner-header">💬 En conversación con ${name}</div>
      <div class="banner-sub">El bot está atendiendo. Cuando confirme una compra, va a crear cotización automáticamente. O podés crearla manual.</div>
      <div class="banner-actions">
        ${escBtn}
        <button class="banner-btn prim" onclick="openManualQuoteModal()">📋 Crear cotización manual</button>
        <button class="banner-btn danger" onclick="confirmArchive()">✕ Archivar conversación</button>
      </div>`;
  } else if(st === 'cotizado' && orderName){
    html = `
      <div class="banner-header">📋 Cotización borrador · ${escapeHtml(orderName)} · ${name}</div>
      <div class="banner-sub">Esperando pago del cliente. Cuando envíe comprobante, el bot lo detecta y marca como pagado automáticamente.</div>
      <div class="banner-actions">
        ${escBtn}
        <a class="banner-btn" target="_blank" href="https://paracarpinteros.odoo.com/odoo/sales/${orderId}">👁 Ver ${escapeHtml(orderName)} en Odoo</a>
        <button class="banner-btn prim" onclick="confirmAndAdvanceModal()">✅ Confirmar venta ${escapeHtml(orderName)} y crear picking</button>
        <button class="banner-btn danger" onclick="confirmArchive()">✕ Archivar</button>
      </div>`;
  } else if(st === 'cotizado'){
    // Estado cotizado pero sin order_name (raro) → ofrecer crear manual
    html = `
      <div class="banner-header">📋 Cotizado · ${name}</div>
      <div class="banner-sub">No tengo número de cotización registrado. Probablemente fue creada antes del tablero.</div>
      <div class="banner-actions">
        ${escBtn}
        <button class="banner-btn prim" onclick="openManualQuoteModal()">📋 Crear cotización en Odoo</button>
      </div>`;
  } else if(st === 'pagado' || st === 'a_despachar'){
    // Render asíncrono con wizard de pasos
    banner.innerHTML = '<div style="padding:6px 0;color:var(--text3);font-size:.78rem">Cargando estado del pedido...</div>';
    renderShipmentWizard(st, name);
    return;
  } else if(st === 'cerrado'){
    html = `
      <div class="banner-header">✅ Conversación archivada · ${name}</div>
      <div class="banner-sub">Esta conversación está cerrada. Si el cliente vuelve a escribir, pasa automáticamente a "En conversación".</div>
      <div class="banner-actions">
        <button class="banner-btn" onclick="markStatus('en_conversacion')">↩ Reabrir conversación</button>
      </div>`;
  }
  // Toggle button para colapsar/expandir
  const toggleIcon = collapsed ? '▼' : '▲';
  const toggleTitle = collapsed ? 'Expandir wizard' : 'Colapsar wizard';
  banner.innerHTML = `<button class="banner-toggle" onclick="toggleBannerCollapse()" title="${toggleTitle}">${toggleIcon}</button>` + html;
}

function toggleBannerCollapse(){
  const banner = document.getElementById('actionBanner');
  if(!banner) return;
  const isCollapsed = banner.classList.toggle('collapsed');
  localStorage.setItem('bannerCollapsed', isCollapsed ? '1' : '0');
  // Cambiar el ícono inmediatamente
  const btn = banner.querySelector('.banner-toggle');
  if(btn){
    btn.textContent = isCollapsed ? '▼' : '▲';
    btn.title = isCollapsed ? 'Expandir wizard' : 'Colapsar wizard';
  }
}

// ──── Wizard de despacho (estados pagado / a_despachar) ────
let CARRIERS_CACHE = null;

async function renderShipmentWizard(st, name){
  const banner = document.getElementById('actionBanner');
  let wiz;
  try{
    wiz = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/wizard`);
  }catch(e){
    banner.innerHTML = `<div style="padding:10px;color:var(--red)">Error cargando wizard: ${e.message}</div>`;
    return;
  }
  if(!wiz?.ok){
    banner.innerHTML = `<div style="padding:10px;color:var(--red)">Error: ${wiz?.error||''}</div>`;
    return;
  }
  const order = wiz.order;
  const orderConfirmed = order && (order.state === 'sale' || order.state === 'done');
  const hasCarrier = order && order.carrier_id;
  // Pagos acumulados (vienen de CURRENT_INFO, que se carga en openConv)
  const payments = CURRENT_INFO?.payments || [];
  const totalPaid = Number(CURRENT_INFO?.total_paid || 0);
  const orderTotal = order ? Number(order.amount_total || 0) : 0;
  const balance = orderTotal - totalPaid;  // positivo: falta cobrar | 0 o negativo: pagado o sobre-pago

  // ──── PASO 1: Producto/cotización ────
  const stepProduct = order ? `
    <div class="wiz-step done">
      <div class="wiz-icon">✓</div>
      <div class="wiz-content">
        <div class="wiz-title">Producto cotizado · ${escapeHtml(order.name)}</div>
        <div class="wiz-sub">${order.lines.length} línea(s) · subtotal productos: ₡${Number(orderTotal - (hasCarrier ? (order.carrier_price||0) : 0)).toLocaleString('es-CR')}${hasCarrier ? ' · ya incluye envío' : ''}</div>
      </div>
    </div>` : `
    <div class="wiz-step blocked">
      <div class="wiz-icon">!</div>
      <div class="wiz-content">
        <div class="wiz-title">Sin cotización</div>
        <div class="wiz-sub">No hay sale.order asociado. Crealo manual para continuar.</div>
        <button class="banner-btn prim" style="margin-top:8px" onclick="openManualQuoteModal()">📋 Crear cotización manual</button>
      </div>
    </div>`;

  // ──── PASO 2: Tipo de envío ────
  let stepShipping = '';
  if(order){
    if(hasCarrier){
      stepShipping = `
        <div class="wiz-step done">
          <div class="wiz-icon">✓</div>
          <div class="wiz-content">
            <div class="wiz-title">Envío: ${escapeHtml(order.carrier_name)}</div>
            <div class="wiz-sub">Total del pedido con envío: <b>₡${orderTotal.toLocaleString('es-CR')}</b></div>
            <button class="banner-btn" style="margin-top:6px;font-size:.7rem;padding:6px 10px" onclick="openCarrierPicker()">Cambiar tipo de envío</button>
          </div>
        </div>`;
    } else {
      stepShipping = `
        <div class="wiz-step current">
          <div class="wiz-icon">2</div>
          <div class="wiz-content">
            <div class="wiz-title">Elegir tipo de envío y agregarlo al pedido</div>
            <div class="wiz-sub">El precio del envío se suma al total. Después el cliente paga el total completo (producto + envío).</div>
            <button class="banner-btn prim" style="margin-top:8px" onclick="openCarrierPicker()">🚚 Elegir método de envío</button>
          </div>
        </div>`;
    }
  }

  // ──── PASO 3: Pago recibido vs total ────
  let stepPayment = '';
  if(order){
    const payLines = payments.length
      ? payments.map(p => `<div style="font-size:.72rem;color:var(--text2);margin-top:3px">• ₡${Number(p.monto_crc||0).toLocaleString('es-CR')} · ${escapeHtml((p.metodo||'').toUpperCase())}${p.banco?' · '+escapeHtml(p.banco):''}${p.referencia?' · Ref '+escapeHtml(p.referencia):''}</div>`).join('')
      : '';
    if(!hasCarrier){
      // Sin envío aún: no podemos saber el total real
      stepPayment = `
        <div class="wiz-step blocked">
          <div class="wiz-icon">3</div>
          <div class="wiz-content">
            <div class="wiz-title-muted">Cobro al cliente</div>
            <div class="wiz-sub">Primero elegí el envío para conocer el total a cobrar. ${payments.length ? `Ya recibido: ₡${totalPaid.toLocaleString('es-CR')}` : ''}</div>
            ${payLines}
          </div>
        </div>`;
    } else if(balance <= 0.5){
      // Pagado completo (con tolerancia 0.5 colón por redondeos)
      stepPayment = `
        <div class="wiz-step done">
          <div class="wiz-icon">✓</div>
          <div class="wiz-content">
            <div class="wiz-title">Pagado completo · ₡${totalPaid.toLocaleString('es-CR')}</div>
            <div class="wiz-sub">Total del pedido: ₡${orderTotal.toLocaleString('es-CR')}${balance < -0.5 ? ` · <span style="color:#fbbf24">sobre-pago de ₡${Math.abs(balance).toLocaleString('es-CR')}</span>` : ''}</div>
            ${payLines}
          </div>
        </div>`;
    } else {
      // Pago parcial
      const partial = totalPaid > 0;
      stepPayment = `
        <div class="wiz-step current">
          <div class="wiz-icon">3</div>
          <div class="wiz-content">
            <div class="wiz-title">${partial ? 'Falta cobrar la diferencia' : 'Cobrar al cliente'}</div>
            <div class="wiz-sub">
              Total del pedido: <b>₡${orderTotal.toLocaleString('es-CR')}</b><br>
              ${partial ? `Ya recibido: ₡${totalPaid.toLocaleString('es-CR')}<br>` : ''}
              <span style="color:#fbbf24">Falta abonar: <b>₡${balance.toLocaleString('es-CR')}</b></span>
            </div>
            ${payLines}
            <button class="banner-btn prim" style="margin-top:8px" onclick="askBalanceModal(${balance})">📨 Avisar al cliente que falta ₡${balance.toLocaleString('es-CR')}</button>
          </div>
        </div>`;
    }
  }

  // ──── PASO 4: Confirmar pedido en Odoo ────
  let stepConfirm = '';
  if(order){
    if(orderConfirmed){
      stepConfirm = `
        <div class="wiz-step done">
          <div class="wiz-icon">✓</div>
          <div class="wiz-content">
            <div class="wiz-title">Pedido confirmado en Odoo</div>
            <div class="wiz-sub">${escapeHtml(order.name)}${order.picking_name ? ' · Picking ' + escapeHtml(order.picking_name) : ''}</div>
            <a class="banner-btn" style="margin-top:6px;display:inline-block;font-size:.7rem;padding:6px 10px" target="_blank" href="${order.url}">👁 Ver en Odoo</a>
          </div>
        </div>`;
    } else if(hasCarrier && balance <= 0.5){
      // Todo listo para confirmar
      stepConfirm = `
        <div class="wiz-step current">
          <div class="wiz-icon">4</div>
          <div class="wiz-content">
            <div class="wiz-title">Confirmar pedido en Odoo</div>
            <div class="wiz-sub">Ya está pagado y con envío asignado. Confirmá la venta para generar el picking.</div>
            <button class="banner-btn prim" style="margin-top:8px" onclick="confirmAndAdvanceModal()">✅ Confirmar venta ${escapeHtml(order.name)} y crear picking</button>
          </div>
        </div>`;
    } else {
      // Bloqueado
      const reason = !hasCarrier ? 'Primero asigná tipo de envío' : (balance > 0.5 ? `Falta cobrar ₡${balance.toLocaleString('es-CR')}` : 'Completar pasos anteriores');
      stepConfirm = `
        <div class="wiz-step blocked">
          <div class="wiz-icon">4</div>
          <div class="wiz-content">
            <div class="wiz-title-muted">Confirmar pedido en Odoo</div>
            <div class="wiz-sub">${reason}</div>
          </div>
        </div>`;
    }
  }

  // ──── PASO 5: Generar guía ────
  let stepGuide = '';
  if(order){
    if(orderConfirmed){
      stepGuide = `
        <div class="wiz-step current">
          <div class="wiz-icon">5</div>
          <div class="wiz-content">
            <div class="wiz-title">Generar guía de envío</div>
            <div class="wiz-sub">Abrí el panel de envíos e imprimí la etiqueta del picking ${escapeHtml(order.picking_name||'')}.</div>
            <a class="banner-btn prim" style="margin-top:8px;display:inline-block" target="_blank" href="https://panel.paracarpinteros.com/panel-envios.html">🚚 Abrir panel de envíos →</a>
            <button class="banner-btn" style="margin-top:8px" onclick="confirmCloseShipment()">✅ Marcar enviado y cerrar</button>
          </div>
        </div>`;
    } else {
      stepGuide = `
        <div class="wiz-step blocked">
          <div class="wiz-icon">5</div>
          <div class="wiz-content">
            <div class="wiz-title-muted">Generar guía de envío</div>
            <div class="wiz-sub">Disponible después de confirmar el pedido en Odoo</div>
          </div>
        </div>`;
    }
  }

  // Header con info clave
  const headerTitle = st === 'pagado'
    ? (balance > 0.5 ? '💰 Pago parcial recibido — preparar envío' : '💰 Pago completo — preparar envío')
    : '📦 Preparar envío';
  const collapsed = localStorage.getItem('bannerCollapsed') === '1';
  banner.className = 'action-banner show banner-' + st + (collapsed ? ' collapsed' : '');
  const toggleIcon = collapsed ? '▼' : '▲';
  const toggleTitle = collapsed ? 'Expandir wizard' : 'Colapsar wizard';
  banner.innerHTML = `
    <button class="banner-toggle" onclick="toggleBannerCollapse()" title="${toggleTitle}">${toggleIcon}</button>
    <div class="banner-header">${headerTitle} · ${name}</div>
    <div class="banner-sub">Flujo: producto → envío → pago completo → confirmar → generar guía.</div>
    <div class="wiz-steps">
      ${stepProduct}
      ${stepShipping}
      ${stepPayment}
      ${stepConfirm}
      ${stepGuide}
    </div>
    <div class="banner-actions" style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="banner-btn ${CURRENT_INFO?.escalated?'warning':''}" onclick="toggleEscalate()">${CURRENT_INFO?.escalated?'⚠ Bot desactivado':'👤 Tomar conversación'}</button>
      ${order ? `<a class="banner-btn" target="_blank" href="${order.url}">👁 Ver ${escapeHtml(order.name)} en Odoo</a>` : ''}
      ${st === 'pagado' ? `<button class="banner-btn warning" onclick="confirmRevertPayment()">❓ El pago no cuadra</button>` : ''}
      <button class="banner-btn danger" onclick="confirmCloseWithoutShipment()" title="Cerrar sin envío — cliente no responde, canceló, lo entregaste a mano, etc.">✕ Cerrar conversación</button>
    </div>`;
}

// Cerrar conversación desde el wizard de despacho sin marcar como enviado.
// Útil cuando el cliente desaparece, lo entregaste a mano, fue contacto telefónico, etc.
function confirmCloseWithoutShipment(){
  if(!CURRENT_PHONE) return;
  if(!confirm('¿Cerrar esta conversación sin generar guía?\n\nNo se marca como enviado en el sistema. Si el cliente vuelve a escribir, reabre automáticamente.')) return;
  markStatus('cerrado');
}

function askBalanceModal(amountDue){
  const name = escapeHtml(CURRENT_INFO?.name || '');
  const orderName = escapeHtml(CURRENT_INFO?.odoo_sale_order_name || '');
  const amt = Math.round(amountDue);
  genModalShow(`
    <div class="gen-modal-box">
      <h3>📨 AVISAR AL CLIENTE LA DIFERENCIA</h3>
      <p>Vas a enviar a <b>${name}</b> un mensaje pidiendo que abone <b>₡${amt.toLocaleString('es-CR')}</b> para completar el pedido ${orderName}.</p>
      <label style="display:block;font-size:.65rem;color:var(--text3);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px;font-weight:700">Nota opcional (datos bancarios, sinpe, etc.)</label>
      <textarea id="balanceNote" class="gen-modal-input" rows="3" placeholder="Ej: SINPE Móvil 8606-9717 (Gabriela Brenes) o BCR cuenta 1234..."></textarea>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="sendBalanceReq(${amt})">📨 Enviar mensaje al cliente</button>
      </div>
    </div>`);
}

async function sendBalanceReq(amountDue){
  const note = document.getElementById('balanceNote')?.value || '';
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/ask-balance`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({amount_due: amountDue, note: note})
    });
    if(r?.ok){
      genModalClose();
      alert('✓ Mensaje enviado al cliente. Esperá su comprobante.');
      openConv(CURRENT_PHONE);
    } else {
      alert('Error: ' + (r?.error||''));
    }
  }catch(e){ alert('Error: '+e.message); }
}

// ───── Enseñar al bot (guardar dato en bot_knowledge, categoría "aprendido") ─────
function openLearnModal(){
  if(!CURRENT_PHONE){ alert('Abrí una conversación primero.'); return; }
  const draft = (document.getElementById('replyText')?.value || '').trim();
  const cliente = escapeHtml(CURRENT_INFO?.name || ('+' + CURRENT_PHONE));
  genModalShow(`
    <div class="gen-modal-box" style="max-width:560px">
      <h3>🎓 ENSEÑAR AL BOT</h3>
      <p style="margin-bottom:12px">Guardá este dato en el conocimiento del bot. Lo usará en <b>todas las conversaciones futuras</b>, no solo con ${cliente}. Escribilo como un hecho general (ej. precio de una pieza, política), no como una respuesta personal.</p>
      <label style="display:block;font-size:.65rem;color:var(--text3);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px;font-weight:700">Título corto</label>
      <input id="learnTitle" class="gen-modal-input" type="text" placeholder="Ej: Precio bisagra pivotante 360°" style="margin-bottom:12px">
      <label style="display:block;font-size:.65rem;color:var(--text3);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px;font-weight:700">Dato a recordar</label>
      <textarea id="learnContent" class="gen-modal-input" rows="4" placeholder="Ej: La bisagra pivotante de 360° (cód. A075) cuesta ₡1.500, se vende por encargo.">${escapeHtml(draft)}</textarea>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="saveLearning()">🎓 Guardar aprendizaje</button>
      </div>
    </div>`);
  setTimeout(() => document.getElementById('learnTitle')?.focus(), 50);
}

async function saveLearning(){
  const title = (document.getElementById('learnTitle')?.value || '').trim();
  const content = (document.getElementById('learnContent')?.value || '').trim();
  if(!title || !content){ alert('Poné un título corto y el dato a recordar.'); return; }
  try{
    const r = await api('/api/knowledge', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({category:'aprendido', title, content})
    });
    if(r?.ok){
      genModalClose();
      alert('✓ Aprendido. El bot lo usará con los clientes a partir de ahora. Podés editarlo o borrarlo en el editor de conocimiento.');
    } else {
      alert('Error: ' + (r?.error||'desconocido'));
    }
  }catch(e){ alert('Error: '+e.message); }
}

async function openCarrierPicker(){
  // Cache carriers
  if(!CARRIERS_CACHE){
    try{
      const r = await api('/api/odoo/carriers');
      if(!r?.ok){ alert('Error cargando carriers: '+(r?.error||'')); return; }
      CARRIERS_CACHE = r.carriers || [];
    }catch(e){ alert('Error: '+e.message); return; }
  }
  // Dos secciones: ASIGNAR uno al pedido (radio-like) vs COTIZAR varios al cliente (checkbox)
  const optionsHtml = CARRIERS_CACHE.map((c,i) => {
    const priceLbl = c.fixed_price
      ? `~₡${Number(c.fixed_price).toLocaleString('es-CR')}`
      : (c.delivery_type === 'base_on_rule' ? 'por peso/zona' : 'según peso');
    return `
      <div class="carrier-row" data-id="${c.id}" style="display:flex;gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--border);border-radius:8px;margin-bottom:6px;background:var(--card)">
        <input type="checkbox" class="cq-chk" id="cq-${c.id}" data-id="${c.id}" style="width:18px;height:18px;cursor:pointer;accent-color:#25D366">
        <label for="cq-${c.id}" style="flex:1;cursor:pointer">
          <div style="font-size:.82rem;font-weight:600">${escapeHtml(c.name)}</div>
          <div style="font-size:.7rem;color:var(--text3);margin-top:2px">${priceLbl}</div>
        </label>
        <button class="banner-btn" style="font-size:.7rem;padding:6px 10px" onclick="selectCarrier(${c.id}, ${JSON.stringify(c.name).replace(/"/g,'&quot;')})">Asignar al pedido</button>
      </div>`;
  }).join('');
  genModalShow(`
    <div class="gen-modal-box" style="max-width:560px">
      <h3>🚚 TIPO DE ENVÍO</h3>
      <p style="margin-bottom:14px"><b>Opción A:</b> "Asignar al pedido" en una sola opción → queda fijada en Odoo.<br>
      <b>Opción B:</b> Marcá varias y pulsá <i>Cotizar al cliente</i> → mandamos las opciones al WhatsApp para que él elija.</p>
      <div style="max-height:50vh;overflow-y:auto;margin:14px 0">${optionsHtml}</div>
      <div class="gen-modal-actions" style="justify-content:space-between">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="quoteShippingToClient()">📨 Cotizar marcadas al cliente</button>
      </div>
    </div>`);
}

async function quoteShippingToClient(){
  const checks = document.querySelectorAll('.cq-chk:checked');
  const ids = Array.from(checks).map(c => parseInt(c.dataset.id));
  if(!ids.length){ alert('Marcá al menos una opción para cotizar al cliente.'); return; }
  if(!confirm(`¿Enviar ${ids.length} opción(es) de envío al cliente por WhatsApp?`)) return;
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/quote-shipping`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({carrier_ids: ids})
    });
    if(r?.ok){
      genModalClose();
      alert(`✓ ${r.options} opción(es) enviadas al cliente. Esperá su respuesta para asignar el envío definitivo.`);
      openConv(CURRENT_PHONE);
    } else {
      alert('Error: ' + (r?.error||''));
    }
  }catch(e){ alert('Error: '+e.message); }
}

async function selectCarrier(carrierId, carrierName){
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/set-carrier`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({carrier_id: carrierId})
    });
    if(r?.ok){
      genModalClose();
      // Recargar wizard
      openConv(CURRENT_PHONE);
    } else {
      alert('Error: ' + (r?.error||''));
    }
  }catch(e){ alert('Error: '+e.message); }
}

function confirmCloseShipment(){
  const name = escapeHtml(CURRENT_INFO?.name || '');
  genModalShow(`
    <div class="gen-modal-box">
      <h3>✅ MARCAR ENVIADO Y CERRAR</h3>
      <p>¿Confirmás que ya generaste la guía y el pedido fue enviado a ${name}?</p>
      <p>Esto archiva la conversación. Si el cliente vuelve a escribir, reaparece automáticamente.</p>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="genModalClose(); markStatus('cerrado')">✅ Marcar enviado y cerrar</button>
      </div>
    </div>`);
}

// ──── Modal genérico ────
function genModalShow(html){
  let m = document.getElementById('genModal');
  if(!m){
    m = document.createElement('div');
    m.id = 'genModal';
    m.className = 'gen-modal-bg';
    m.onclick = (e) => { if(e.target === m) genModalClose(); };
    document.body.appendChild(m);
  }
  m.innerHTML = html;
  m.classList.add('show');
}
function genModalClose(){
  const m = document.getElementById('genModal');
  if(m) m.classList.remove('show');
}

// ──── Cotización manual ────
function openManualQuoteModal(){
  if(!CURRENT_PHONE) return;
  const name = escapeHtml(CURRENT_INFO?.name || '+' + CURRENT_PHONE);
  genModalShow(`
    <div class="gen-modal-box">
      <h3>📋 COTIZACIÓN MANUAL · ${name}</h3>
      <p>Ingresá uno o varios productos con su código de Odoo (default_code) y cantidad. La cotización se crea en estado borrador y queda asociada al cliente.</p>
      <div id="mqItems">${mqRow()}</div>
      <button class="banner-btn" onclick="addMQRow()" style="margin-bottom:14px">+ Agregar otro producto</button>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="submitManualQuote()">📋 Crear cotización para ${name}</button>
      </div>
    </div>`);
  setTimeout(()=>document.querySelector('.mq-code')?.focus(), 50);
}
function mqRow(){
  return `<div class="mq-row" style="display:flex;gap:8px;margin-bottom:8px">
    <input class="gen-modal-input mq-code" placeholder="Código (ej. A805)" style="flex:1">
    <input class="gen-modal-input mq-qty" placeholder="Cant." type="number" value="1" min="1" style="width:80px">
    <button class="banner-btn" onclick="this.parentElement.remove()" title="Quitar">✕</button>
  </div>`;
}
function addMQRow(){
  document.getElementById('mqItems').insertAdjacentHTML('beforeend', mqRow());
}
async function submitManualQuote(){
  const rows = document.querySelectorAll('.mq-row');
  const items = [];
  rows.forEach(r => {
    const code = r.querySelector('.mq-code').value.trim();
    const qty = parseFloat(r.querySelector('.mq-qty').value) || 1;
    if(code) items.push({codigo: code, cantidad: qty});
  });
  if(!items.length){ alert('Agregá al menos un producto'); return; }
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/manual-quote`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({items})
    });
    if(r?.ok){
      genModalClose();
      alert(`✓ Cotización ${r.order_name} creada · ₡${Number(r.total_crc).toLocaleString('es-CR')}`);
      openConv(CURRENT_PHONE);
    } else {
      alert('Error: ' + (r?.error || 'desconocido'));
    }
  }catch(e){ alert('Error: ' + e.message); }
}

// ──── Confirmaciones destructivas ────
function confirmAndAdvanceModal(){
  const name = escapeHtml(CURRENT_INFO?.name || '');
  const order = escapeHtml(CURRENT_INFO?.odoo_sale_order_name || '');
  genModalShow(`
    <div class="gen-modal-box">
      <h3>✅ CONFIRMAR VENTA EN ODOO</h3>
      <p>Vas a confirmar la cotización <b>${order}</b> de <b>${name}</b>.</p>
      <ul>
        <li>El sale.order pasa de "Borrador" a "Confirmado"</li>
        <li>Se genera automáticamente el picking de salida</li>
        <li>La conversación pasa a 📦 A despachar</li>
      </ul>
      <div class="warn-text">⚠ Esta acción es difícil de deshacer en Odoo.</div>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="genModalClose(); doConfirmOrder()">✅ Confirmar ${order}</button>
      </div>
    </div>`);
}
async function doConfirmOrder(){
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/confirm-order`, {method:'POST'});
    if(r?.ok){
      alert(`✓ Confirmado: ${r.order_name}
Picking generado: ${r.picking_name}`);
      markStatus('a_despachar');
    } else {
      alert('Error: ' + (r?.error || 'desconocido'));
    }
  }catch(e){ alert('Error: ' + e.message); }
}
function confirmArchive(){
  const name = escapeHtml(CURRENT_INFO?.name || '');
  genModalShow(`
    <div class="gen-modal-box">
      <h3>✕ ARCHIVAR CONVERSACIÓN</h3>
      <p>¿Marcar la conversación con <b>${name}</b> como cerrada?</p>
      <p>Si el cliente vuelve a escribir, la conversación reaparece automáticamente en "En conversación".</p>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn danger" onclick="genModalClose(); markStatus('cerrado')">✕ Archivar</button>
      </div>
    </div>`);
}
function confirmRevertPayment(){
  genModalShow(`
    <div class="gen-modal-box">
      <h3>❓ PAGO NO CUADRA</h3>
      <p>Vas a revertir la conversación a "En conversación" para revisar con el cliente.</p>
      <p>El comprobante queda guardado en el historial. La conversación deja de aparecer en "💰 Pagado".</p>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn warning" onclick="genModalClose(); markStatus('en_conversacion')">↩ Revertir a "En conversación"</button>
      </div>
    </div>`);
}

// ──── Drawer info partner con datos EDITABLES ────
let PARTNER_FULL_CACHE = null;

async function openPartnerDrawer(){
  let drawer = document.getElementById('partnerDrawer');
  if(!drawer){
    drawer = document.createElement('div');
    drawer.id = 'partnerDrawer';
    drawer.className = 'partner-drawer';
    drawer.style.width = '420px';
    document.body.appendChild(drawer);
  }
  drawer.classList.add('open');
  const info = CURRENT_INFO;
  const p = CURRENT_PARTNER;
  const phone = CURRENT_PHONE || '';
  if(!info){
    drawer.innerHTML = '<div class="drawer-head"><div class="drawer-title">FICHA</div><button class="close-x" onclick="closePartnerDrawer()">✕</button></div><div class="drawer-body">Sin datos</div>';
    return;
  }
  drawer.innerHTML = '<div class="drawer-head"><div class="drawer-title">FICHA DEL CLIENTE</div><div style="display:flex;gap:4px"><button class="close-x" onclick="openPartnerDrawer()" title="Refrescar datos">↻</button><button class="close-x" onclick="closePartnerDrawer()">✕</button></div></div><div class="drawer-body" id="partnerDrawerBody"><div style="padding:20px;text-align:center;color:var(--text2)">Cargando…</div></div>';
  // Cargar datos completos del partner si hay ID
  let full = null;
  if(info.odoo_partner_id){
    try{
      const r = await api('/api/partner/' + info.odoo_partner_id + '/full');
      if(r?.ok) full = r;
    }catch(e){ console.warn('partner full', e); }
  }
  PARTNER_FULL_CACHE = full;
  renderPartnerDrawerBody(full);
}

function renderPartnerDrawerBody(full){
  const body = document.getElementById('partnerDrawerBody');
  if(!body) return;
  const info = CURRENT_INFO;
  const phone = CURRENT_PHONE || '';
  const payment = info.payment_meta_parsed;
  const payments = info.payments || [];
  const totalPaid = info.total_paid || 0;
  const partnerId = info.odoo_partner_id;
  const f = full || {};

  body.innerHTML = `
    <!-- Datos editables del partner -->
    <div class="psec">
      <h4>Nombre</h4>
      <input class="pf-inp" id="pf-name" value="${escapeHtml(f.name || info.name || '')}" placeholder="Nombre completo">
    </div>
    <div class="psec">
      <h4>WhatsApp (no editable)</h4>
      <div class="val">+${escapeHtml(phone)}</div>
    </div>
    <div class="psec">
      <h4>Email</h4>
      <input class="pf-inp" id="pf-email" value="${escapeHtml(f.email || '')}" placeholder="correo@ejemplo.com">
    </div>
    <div class="psec">
      <h4>Teléfono adicional</h4>
      <input class="pf-inp" id="pf-phone" value="${escapeHtml(f.phone || ('+' + phone))}" placeholder="+506 XXXX-XXXX">
    </div>
    <div class="psec">
      <h4>Dirección</h4>
      <input class="pf-inp" id="pf-street" value="${escapeHtml(f.street || '')}" placeholder="Calle, número, señas" style="margin-bottom:6px">
      <input class="pf-inp" id="pf-street2" value="${escapeHtml(f.street2 || '')}" placeholder="Detalles (opcional)">
    </div>
    <div class="psec" style="display:flex;gap:6px">
      <div style="flex:2">
        <h4>Ciudad / Cantón</h4>
        <input class="pf-inp" id="pf-city" value="${escapeHtml(f.city || '')}" placeholder="Ej: Turrialba">
      </div>
      <div style="flex:1">
        <h4>CP</h4>
        <input class="pf-inp" id="pf-zip" value="${escapeHtml(f.zip || '')}" placeholder="30504">
      </div>
    </div>
    ${f.state || f.country ? `<div class="psec"><h4>Provincia / País</h4><div class="val muted">${escapeHtml(f.state||'')}${f.state && f.country?' · ':''}${escapeHtml(f.country||'')}</div></div>` : ''}

    <button class="banner-btn prim" onclick="savePartnerChanges(${partnerId})" style="width:100%;margin-bottom:8px">💾 Guardar cambios en Odoo</button>

    <div style="border-top:1px solid var(--border);margin:14px 0"></div>

    <!-- Estado / pedido en curso -->
    <div class="psec">
      <h4>Estado conversación</h4>
      <div class="val"><span class="status-badge sb-${info.status||'nuevo'}">${STATUS_LABELS_FULL[info.status||'nuevo']}</span></div>
    </div>
    ${full ? `<div class="psec"><h4>Historial Odoo</h4><div class="val">${f.sale_count||0} pedidos · ₡${Math.round(f.total_invoiced||0).toLocaleString('es-CR')} facturado</div></div>` : ''}
    ${info.odoo_sale_order_name ? `
      <div class="psec">
        <h4>Cotización en curso</h4>
        <div class="val">${escapeHtml(info.odoo_sale_order_name)}</div>
        <a class="banner-btn" target="_blank" href="https://paracarpinteros.odoo.com/odoo/sales/${info.odoo_sale_order_id}" style="display:inline-block;margin-top:6px;font-size:.72rem">Abrir cotización en Odoo</a>
      </div>` : ''}

    ${payments.length ? `
      <div class="psec">
        <h4>Pagos recibidos · ₡${totalPaid.toLocaleString('es-CR')}</h4>
        ${payments.map(p => `<div class="val muted" style="margin-top:3px">• ₡${Number(p.monto_crc||0).toLocaleString('es-CR')} · ${escapeHtml((p.metodo||'').toUpperCase())}${p.banco?' · '+escapeHtml(p.banco):''}${p.referencia?' · Ref '+escapeHtml(p.referencia):''}</div>`).join('')}
      </div>` : ''}

    <div style="border-top:1px solid var(--border);margin:14px 0"></div>

    <!-- Calculadora de envío -->
    <div class="psec">
      <h4>💰 Calculadora de envío</h4>
      <p style="font-size:.78rem;color:var(--text2);margin-bottom:8px">Estima el costo del envío según carrier + peso aproximado.</p>
      <div style="display:flex;gap:6px;margin-bottom:8px">
        <select id="calcCarrier" class="pf-inp" style="flex:2">
          <option value="">Elegir carrier...</option>
        </select>
        <input id="calcWeight" class="pf-inp" type="number" placeholder="Peso (g)" value="500" style="flex:1">
      </div>
      <button class="banner-btn" onclick="calcShipping()" style="width:100%;margin-bottom:6px">Calcular precio</button>
      <div id="calcResult" style="font-size:.82rem;color:var(--text);margin-top:8px"></div>
    </div>

    <div style="border-top:1px solid var(--border);margin:14px 0"></div>

    <!-- Atajos del flujo -->
    <div class="psec">
      <h4>Acciones rápidas</h4>
      <button class="banner-btn" onclick="closePartnerDrawer(); openManualQuoteModal()" style="width:100%;margin-bottom:6px">📋 Crear cotización manual</button>
      ${info.odoo_sale_order_id ? `<button class="banner-btn" onclick="closePartnerDrawer(); openCarrierPicker()" style="width:100%;margin-bottom:6px">🚚 Elegir/cotizar envío</button>` : ''}
      ${info.status === 'a_despachar' ? `<a class="banner-btn prim" target="_blank" href="https://panel.paracarpinteros.com/panel-envios.html" style="width:100%;margin-bottom:6px;text-align:center;text-decoration:none">📦 Generar guía ahora →</a>` : ''}
    </div>

    ${partnerId ? `<a class="banner-btn" target="_blank" href="https://paracarpinteros.odoo.com/odoo/contacts/${partnerId}" style="display:block;margin-top:14px;text-align:center">Ver partner #${partnerId} en Odoo →</a>` : ''}
  `;
  // Llenar carriers en el dropdown
  loadCarriersForCalc();
}

async function savePartnerChanges(partnerId){
  if(!partnerId){ alert('Sin partner_id'); return; }
  const data = {
    name: document.getElementById('pf-name').value.trim(),
    email: document.getElementById('pf-email').value.trim(),
    phone: document.getElementById('pf-phone').value.trim(),
    street: document.getElementById('pf-street').value.trim(),
    street2: document.getElementById('pf-street2').value.trim(),
    city: document.getElementById('pf-city').value.trim(),
    zip: document.getElementById('pf-zip').value.trim(),
  };
  try{
    const r = await api('/api/partner/' + partnerId + '/update', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    if(r?.ok){
      alert('✓ Guardado en Odoo: ' + (r.updated || []).join(', '));
      openConv(CURRENT_PHONE);
    } else {
      alert('Error: ' + (r?.error || ''));
    }
  }catch(e){ alert('Error: '+e.message); }
}

async function loadCarriersForCalc(){
  const sel = document.getElementById('calcCarrier');
  if(!sel) return;
  try{
    if(!CARRIERS_CACHE){
      const r = await api('/api/odoo/carriers');
      CARRIERS_CACHE = r?.carriers || [];
    }
    sel.innerHTML = '<option value="">Elegir carrier...</option>' + CARRIERS_CACHE.map(c =>
      `<option value="${c.id}">${escapeHtml(c.name)}</option>`
    ).join('');
  }catch(e){}
}

async function calcShipping(){
  const carrierId = document.getElementById('calcCarrier').value;
  const weight = parseFloat(document.getElementById('calcWeight').value || 500);
  const res = document.getElementById('calcResult');
  if(!carrierId){ res.innerHTML = '<span style="color:var(--text2)">Elegí un carrier</span>'; return; }
  try{
    const r = await api(`/api/odoo/carriers/${carrierId}/quote`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({weight_g: weight, partner_id: CURRENT_INFO?.odoo_partner_id})
    });
    if(r?.ok){
      let extra = '';
      if(r.delivery_type === 'zone_based'){
        // Dual: mostrar zona derivada + carrier recomendado
        extra = `<div style="font-size:.78rem; color:var(--text2); margin-top:4px">
          Zona: <b>${escapeHtml(r.zone_name||'?')}</b> · ${escapeHtml(r.rango_peso||'')}
          ${r.home_extra ? '+ ₡'+Number(r.home_extra).toLocaleString('es-CR')+' domicilio' : (r.home_delivery ? '· domicilio GRATIS' : '· retiro sucursal')}
        </div>`;
      } else if(r.delivery_type === 'base_on_rule'){
        extra = ' (estimado · varía por zona/peso real)';
      }
      res.innerHTML = `<div style="padding:8px 10px;background:#e8f7ee;border:1px solid #80d4ad;border-radius:6px"><b>${escapeHtml(r.carrier_name)}</b>: ₡${Number(r.price).toLocaleString('es-CR')}${typeof extra === 'string' ? extra : ''}${typeof extra !== 'string' ? extra : ''}</div>`;
    } else {
      res.innerHTML = `<span style="color:var(--red)">Error: ${r?.error||''}</span>`;
    }
  }catch(e){ res.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`; }
}
function closePartnerDrawer(){
  const d = document.getElementById('partnerDrawer');
  if(d) d.classList.remove('open');
}

// ──── Knowledge Base ────
async function openKnowledgeDrawer(){
  let drawer = document.getElementById('knowledgeDrawer');
  if(!drawer){
    drawer = document.createElement('div');
    drawer.id = 'knowledgeDrawer';
    drawer.className = 'partner-drawer';
    drawer.style.width = '500px';
    document.body.appendChild(drawer);
  }
  drawer.innerHTML = `
    <div class="drawer-head">
      <div class="drawer-title">📚 CONOCIMIENTOS DEL BOT</div>
      <button class="close-x" onclick="closeKnowledgeDrawer()">✕</button>
    </div>
    <div class="drawer-body" id="kbBody">
      <div style="text-align:center;color:var(--text2);padding:20px">Cargando...</div>
    </div>`;
  drawer.classList.add('open');
  try{
    const items = await api('/api/knowledge');
    renderKnowledge(items || []);
  }catch(e){
    document.getElementById('kbBody').innerHTML = `<div style="color:var(--red);padding:10px">Error: ${e.message}</div>`;
  }
}
function closeKnowledgeDrawer(){
  const d = document.getElementById('knowledgeDrawer');
  if(d) d.classList.remove('open');
}

// ───────── TERMÓMETRO META ─────────
async function openMetaDrawer(force){
  let drawer = document.getElementById('metaDrawer');
  if(!drawer){
    drawer = document.createElement('div');
    drawer.id = 'metaDrawer';
    drawer.className = 'partner-drawer';
    drawer.style.width = '520px';
    document.body.appendChild(drawer);
  }
  drawer.innerHTML = `
    <div class="drawer-head">
      <div class="drawer-title">📡 ESTADO CUENTA WHATSAPP</div>
      <button class="close-x" onclick="closeMetaDrawer()">✕</button>
    </div>
    <div class="drawer-body" id="metaBody">
      <div style="text-align:center;color:var(--text2);padding:20px">Cargando…</div>
    </div>`;
  drawer.classList.add('open');
  try{
    const [d, modeInfo] = await Promise.all([
      api('/api/meta/health' + (force ? '?force=1' : '')),
      api('/api/bot/mode'),
    ]);
    renderMetaHealth(d, modeInfo);
  }catch(e){
    document.getElementById('metaBody').innerHTML = `<div style="color:var(--red);padding:10px">Error: ${e.message}</div>`;
  }
}

async function setBotMode(mode){
  // Doble confirm si es el destructivo
  if(mode === 'escalate_all'){
    const msg = [
      '⚠️ Vas a desactivar todas las respuestas automáticas del bot.',
      '',
      'Todos los mensajes entrantes quedarán "sin leer" y un humano tendrá que contestar cada uno.',
      '',
      '¿Confirmás?'
    ].join(String.fromCharCode(10));
    if(!confirm(msg)) return;
  }
  try{
    const r = await api('/api/bot/mode', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({mode})
    });
    if(r && r.ok){
      openMetaDrawer(); // refrescar drawer y chip
      updateMetaChip();
    }
  }catch(e){
    alert('No se pudo cambiar el modo: ' + e.message);
  }
}
function closeMetaDrawer(){
  const d = document.getElementById('metaDrawer');
  if(d) d.classList.remove('open');
}

// ───────── NUEVO CONTACTO (lead pre-cargado + wa.me link) ─────────
async function openNewContactDrawer(){
  let drawer = document.getElementById('newContactDrawer');
  if(!drawer){
    drawer = document.createElement('div');
    drawer.id = 'newContactDrawer';
    drawer.className = 'partner-drawer';
    drawer.style.width = '480px';
    document.body.appendChild(drawer);
  }
  drawer.innerHTML = `
    <div class="drawer-head">
      <div class="drawer-title">➕ NUEVO CONTACTO</div>
      <button class="close-x" onclick="closeNewContactDrawer()">✕</button>
    </div>
    <div class="drawer-body">
      <p style="font-size:.78rem;color:var(--text2);line-height:1.5;margin-bottom:14px">
        Cargá un lead en el panel + obtené un link wa.me que podés compartir para que <strong>el cliente te escriba primero</strong> (cumple política de Meta: no se envía nada proactivo).
      </p>

      <div style="margin-bottom:12px">
        <label style="font-size:.72rem;color:var(--text2);font-weight:600;display:block;margin-bottom:4px">NOMBRE *</label>
        <input id="nc_name" class="pf-inp" style="width:100%" placeholder="Ej: Juan Pérez" maxlength="120">
      </div>

      <div style="margin-bottom:12px">
        <label style="font-size:.72rem;color:var(--text2);font-weight:600;display:block;margin-bottom:4px">TELÉFONO *</label>
        <input id="nc_phone" class="pf-inp" style="width:100%;font-family:monospace" placeholder="86069717 o +506 8606 9717" maxlength="20">
        <div style="font-size:.65rem;color:var(--text3);margin-top:3px">Si es CR sin prefijo, se agrega 506 automáticamente</div>
      </div>

      <div style="margin-bottom:12px">
        <label style="font-size:.72rem;color:var(--text2);font-weight:600;display:block;margin-bottom:4px">NOTA INTERNA (opcional)</label>
        <textarea id="nc_note" class="pf-inp" style="width:100%;min-height:60px" placeholder="Ej: Vino por la tapeteadora A704, quedó en confirmar peso..." maxlength="500"></textarea>
        <div style="font-size:.65rem;color:var(--text3);margin-top:3px">No se envía al cliente. Queda como mensaje informativo en la conversación.</div>
      </div>

      <div style="margin-bottom:14px">
        <label style="font-size:.72rem;color:var(--text2);font-weight:600;display:block;margin-bottom:4px">MENSAJE PRE-LLENADO PARA wa.me (opcional)</label>
        <textarea id="nc_wa_message" class="pf-inp" style="width:100%;min-height:60px" placeholder="Hola {nombre}, te escribimos de Paracarpinteros 👋" maxlength="300"></textarea>
        <div style="font-size:.65rem;color:var(--text3);margin-top:3px">Es lo que va a aparecer pre-escrito en el WhatsApp del cliente cuando toque el link. Si lo dejás vacío, se usa un saludo genérico.</div>
      </div>

      <button class="banner-btn prim" id="nc_submit" onclick="submitNewContact()" style="width:100%">Crear contacto</button>

      <div id="nc_result" style="margin-top:16px"></div>
    </div>`;
  drawer.classList.add('open');
  setTimeout(() => document.getElementById('nc_name').focus(), 100);
}

function closeNewContactDrawer(){
  const d = document.getElementById('newContactDrawer');
  if(d) d.classList.remove('open');
}

async function submitNewContact(){
  const name = document.getElementById('nc_name').value.trim();
  const phone = document.getElementById('nc_phone').value.trim();
  const note = document.getElementById('nc_note').value.trim();
  const waMsg = document.getElementById('nc_wa_message').value.trim();
  const resultEl = document.getElementById('nc_result');
  const submitBtn = document.getElementById('nc_submit');

  if(!name){ resultEl.innerHTML = '<div style="color:var(--red);font-size:.78rem">Falta el nombre</div>'; return; }
  if(!phone){ resultEl.innerHTML = '<div style="color:var(--red);font-size:.78rem">Falta el teléfono</div>'; return; }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Creando...';
  resultEl.innerHTML = '';

  try{
    const r = await api('/api/conversation/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({phone, name, note, wa_message: waMsg}),
    });
    if(!r || !r.ok){ throw new Error((r && r.error) || 'Error desconocido'); }

    const partnerStr = r.partner_id ? `<span style="color:var(--green);font-weight:600">Partner Odoo #${r.partner_id}</span>` : '<span style="color:var(--yellow)">Sin partner Odoo</span>';
    const statusStr = r.created_new ? '✅ Contacto nuevo creado' : '↻ Contacto actualizado (ya existía en el panel)';

    resultEl.innerHTML = `
      <div style="background:#e8f5e9;border:1px solid #a8e5b8;border-radius:8px;padding:14px;margin-bottom:10px">
        <div style="font-size:.85rem;font-weight:600;color:#0f5e1f;margin-bottom:6px">${statusStr}</div>
        <div style="font-size:.72rem;color:var(--text2);line-height:1.5">
          <strong>${escapeHtml(r.name)}</strong> · <span style="font-family:monospace">+${r.phone}</span><br>
          ${partnerStr}
        </div>
      </div>

      <div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:10px">
        <div style="font-size:.72rem;text-transform:uppercase;color:var(--text2);font-weight:600;margin-bottom:6px">Link wa.me para compartir</div>
        <div style="font-size:.7rem;color:var(--text3);font-family:monospace;word-break:break-all;background:#fff;padding:8px;border-radius:4px;margin-bottom:10px">${escapeHtml(r.wa_link)}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="banner-btn prim" onclick="copyToClip('${r.wa_link.replace(/'/g, "\\'")}')">📋 Copiar link</button>
          <button class="banner-btn" onclick="window.open('${r.wa_link.replace(/'/g, "\\'")}', '_blank')">↗ Abrir en mi WhatsApp</button>
        </div>
        <div style="font-size:.65rem;color:var(--text3);margin-top:8px;line-height:1.5">
          💡 Pasale el link al cliente por mail, SMS o redes. Cuando toque el link, le abre WhatsApp con el mensaje pre-escrito y nuestro número como destinatario. Apenas envíe, el bot lo atiende con el contexto que cargaste.
        </div>
      </div>

      <div style="display:flex;gap:6px">
        <button class="banner-btn" onclick="openConv('${r.phone}')">Ver conversación en el panel</button>
        <button class="banner-btn" onclick="resetNewContactForm()">+ Otro contacto</button>
      </div>
    `;

    // Refrescar lista de conversaciones para que aparezca la nueva
    loadConvs();
    loadStats();
  }catch(e){
    resultEl.innerHTML = `<div style="color:var(--red);font-size:.78rem;padding:8px;background:#fde2e2;border-radius:6px">Error: ${escapeHtml(e.message || String(e))}</div>`;
  }finally{
    submitBtn.disabled = false;
    submitBtn.textContent = 'Crear contacto';
  }
}

function resetNewContactForm(){
  document.getElementById('nc_name').value = '';
  document.getElementById('nc_phone').value = '';
  document.getElementById('nc_note').value = '';
  document.getElementById('nc_wa_message').value = '';
  document.getElementById('nc_result').innerHTML = '';
  document.getElementById('nc_name').focus();
}

async function copyToClip(text){
  try{
    await navigator.clipboard.writeText(text);
    // Mini toast
    const t = document.createElement('div');
    t.textContent = '✓ Copiado al portapapeles';
    t.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:var(--green);color:#fff;padding:10px 18px;border-radius:8px;font:600 .82rem inherit;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,.25)';
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2000);
  }catch(e){
    alert('No se pudo copiar: ' + e.message);
  }
}
function metaPill(level, label){
  const colors = {
    ok:    {bg:'#c5f0bd', fg:'#0f5e1f', dot:'#1bb24a'},
    warn:  {bg:'#fff2c2', fg:'#7a5a00', dot:'#d49a00'},
    err:   {bg:'#fde2e2', fg:'#921313', dot:'#c53030'},
    muted: {bg:'#e1e6ea', fg:'#3b4a54', dot:'#8696a0'},
  };
  const c = colors[level] || colors.muted;
  return `<span style="display:inline-flex;align-items:center;gap:6px;background:${c.bg};color:${c.fg};border-radius:12px;padding:3px 10px;font:600 .72rem inherit">
    <span style="width:8px;height:8px;border-radius:50%;background:${c.dot}"></span>${label}
  </span>`;
}
function renderMetaHealth(d, modeInfo){
  if(!d || !d.ok){
    document.getElementById('metaBody').innerHTML = '<div style="color:var(--red);padding:10px">Sin datos</div>';
    return;
  }
  const p = d.phone || {};
  const w = d.waba || {};
  const t = d.templates || {};
  const a = d.analytics_7d || {};
  const currentMode = (modeInfo && modeInfo.mode) || 'normal';
  const modes = (modeInfo && modeInfo.modes) || {};

  // Mapas auxiliares
  const verifMap = {verified:'ok', pending:'warn', failed:'err', rejected:'err', unverified:'muted'};
  const verifLevel = verifMap[(w.verification||'').toLowerCase()] || 'muted';
  const throughputLabel = ({STANDARD:'STANDARD · 80 msg/s', HIGH:'HIGH · 1000 msg/s'})[p.throughput] || (p.throughput || '—');
  const nameMap = {APPROVED:'ok', PENDING:'warn', REJECTED:'err'};
  const nameLevel = nameMap[p.name_status] || 'muted';

  const tmplItems = (t.items || []).slice(0, 12).map(it => {
    const sLevel = ({APPROVED:'ok', PENDING:'warn', REJECTED:'err', IN_APPEAL:'warn', PENDING_DELETION:'warn'})[it.status] || 'muted';
    const qScore = (it.quality || 'UNKNOWN');
    return `<tr>
      <td style="padding:6px 4px;font-family:monospace;font-size:.78rem">${escapeHtml(it.name || '—')}</td>
      <td style="padding:6px 4px">${metaPill(sLevel, it.status || '—')}</td>
      <td style="padding:6px 4px;font-size:.72rem;color:var(--text2)">${it.category||'—'} · ${it.language||''}</td>
      <td style="padding:6px 4px;font-size:.72rem;color:var(--text2)">${qScore}</td>
    </tr>`;
  }).join('');

  const catRows = Object.entries(a.by_category || {}).map(([k,v]) =>
    `<tr><td style="padding:4px 4px;font-size:.78rem">${k}</td><td style="padding:4px 4px;text-align:right;font-weight:600">${v}</td></tr>`
  ).join('') || '<tr><td colspan="2" style="color:var(--text3);padding:8px;text-align:center;font-size:.78rem">Sin conversaciones aún en este número</td></tr>';

  const ageStr = d.cached ? `(cache ${d.age_s||0}s)` : '(fresco)';

  // Sugerencia de modo según quality
  const qLevel = (p.quality && p.quality.level) || 'muted';
  const suggested = qLevel === 'err' ? 'escalate_all' : (qLevel === 'warn' ? 'conservative' : 'normal');
  const modeColors = {
    normal:       {bg:'#c5f0bd', border:'#7ac28a', fg:'#0f5e1f', icon:'🤖'},
    conservative: {bg:'#fff2c2', border:'#f1d488', fg:'#7a5a00', icon:'🛡️'},
    escalate_all: {bg:'#fde2e2', border:'#f8b4b4', fg:'#921313', icon:'🚨'},
  };
  const modeButtons = ['normal','conservative','escalate_all'].map(k => {
    const m = modes[k] || {label:k, desc:''};
    const c = modeColors[k];
    const active = currentMode === k;
    const isSuggested = suggested === k && !active;
    return `<button onclick="setBotMode('${k}')" style="
        display:block;width:100%;text-align:left;
        background:${active?c.bg:'#fff'};
        border:2px solid ${active?c.border:'var(--border)'};
        color:${active?c.fg:'var(--text)'};
        border-radius:10px;padding:11px 13px;margin-bottom:8px;cursor:pointer;
        font-family:inherit;transition:.15s;position:relative;
      "${active?'':' onmouseover="this.style.background=\'#f5f6f6\'" onmouseout="this.style.background=\'#fff\'"'}>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="font-size:1.05rem">${c.icon}</span>
        <strong style="font-size:.92rem">${m.label}</strong>
        ${active ? '<span style="margin-left:auto;font-size:.65rem;font-weight:700;letter-spacing:.5px">● ACTIVO</span>' : ''}
        ${isSuggested ? '<span style="margin-left:auto;background:#3b6cb5;color:#fff;font-size:.6rem;font-weight:700;padding:2px 6px;border-radius:8px">SUGERIDO</span>' : ''}
      </div>
      <div style="font-size:.74rem;color:${active?c.fg:'var(--text2)'};line-height:1.45">${escapeHtml(m.desc||'')}</div>
    </button>`;
  }).join('');

  document.getElementById('metaBody').innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div style="font-size:.7rem;color:var(--text3)">${ageStr}</div>
      <button class="action-btn" onclick="openMetaDrawer(true)">↻ Refrescar</button>
    </div>

    <!-- MODO DEL BOT — sección destacada, lo más accionable -->
    <div style="background:#fff;border:2px solid var(--border);border-radius:10px;padding:12px;margin-bottom:14px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div style="font-size:.78rem;font-weight:700;color:var(--text);text-transform:uppercase;letter-spacing:.5px">Modo de respuesta del bot</div>
        ${metaPill(qLevel, 'Quality: ' + ((p.quality && p.quality.raw) || '—'))}
      </div>
      ${modeButtons}
      <div style="font-size:.7rem;color:var(--text3);line-height:1.5;margin-top:8px;padding:8px;background:var(--card);border-radius:6px">
        💡 El bot se adapta al estado de la cuenta en Meta. Si <strong>Quality baja a YELLOW</strong>, te sugerimos <strong>Conservador</strong>. Si baja a <strong>RED</strong>, pasar a <strong>Solo humano</strong> mientras se diagnostica qué disparó el problema. El cambio es instantáneo y aplica al próximo mensaje entrante.
      </div>
    </div>

    <div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:12px">
      <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text2);font-weight:600;margin-bottom:8px">Número</div>
      <div style="font-size:1.05rem;font-weight:600;color:var(--text);font-family:monospace">${p.number || '—'}</div>
      <div style="font-size:.78rem;color:var(--text2);margin-top:2px">${p.verified_name || '—'}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">
        ${metaPill(p.quality?.level || 'muted', 'Quality · ' + (p.quality?.raw || '—'))}
        ${metaPill(nameLevel, 'Nombre · ' + (p.name_status || '—'))}
        ${metaPill('muted', throughputLabel)}
      </div>
      <div style="font-size:.66rem;color:var(--text3);margin-top:8px">${(p.quality && p.quality.label) || ''}</div>
    </div>

    <div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:12px">
      <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text2);font-weight:600;margin-bottom:8px">Cuenta de empresa (WABA)</div>
      <div style="font-size:.92rem;font-weight:500;color:var(--text)">${escapeHtml(w.business || w.name || '—')}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">
        ${metaPill(verifLevel, 'Verificación · ' + (w.verification || '—'))}
        ${w.business_status ? metaPill(w.business_status==='APPROVED'?'ok':'warn', 'Negocio · ' + w.business_status) : ''}
        ${w.ownership ? metaPill('muted', 'Ownership · ' + w.ownership) : ''}
      </div>
    </div>

    <div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text2);font-weight:600">Plantillas (${t.total||0})</div>
        <div style="font-size:.7rem;color:var(--text2)">
          ${(t.approved||0)} OK · ${(t.pending||0)} pend · ${(t.rejected||0)} rej
        </div>
      </div>
      ${tmplItems
        ? `<table style="width:100%;border-collapse:collapse"><tbody>${tmplItems}</tbody></table>`
        : '<div style="color:var(--text3);padding:8px;text-align:center;font-size:.78rem">Sin plantillas</div>'}
    </div>

    <div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text2);font-weight:600">Últimos 7 días</div>
        <div style="font-size:.7rem;color:var(--text2)">${a.total||0} conversaciones${a.cost ? (' · $' + a.cost) : ''}</div>
      </div>
      <table style="width:100%;border-collapse:collapse"><tbody>${catRows}</tbody></table>
    </div>

    <div style="font-size:.7rem;color:var(--text3);line-height:1.5;padding:6px 4px">
      Cache 5 min. Refrescá manualmente si querés datos en vivo.
      Quality rating <strong>RED/YELLOW</strong> = pausá los proactivos y revisá las últimas respuestas del bot.
    </div>
  `;
}
function renderKnowledge(items){
  const body = document.getElementById('kbBody');
  const info = `
    <p style="font-size:.78rem;color:var(--text2);line-height:1.5;margin-bottom:14px">
      Estos textos se le pasan al bot en cada respuesta para que tenga el contexto correcto sobre tu empresa. Editá lo que no sea cierto (ej. si dice algo de un local en San José que no existe), agregá nuevos puntos, o desactivá los que no querés usar.
    </p>
    <button class="banner-btn prim" onclick="addKnowledge()" style="margin-bottom:18px;width:100%">+ Agregar nuevo conocimiento</button>
  `;
  const list = items.map(k => `
    <div class="kb-item" style="border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:10px;background:var(--card);opacity:${k.active?'1':'.5'}">
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
        <span style="background:#e1edff;color:#3b6cb5;padding:2px 8px;border-radius:10px;font-size:.62rem;font-weight:600;text-transform:uppercase">${escapeHtml(k.category)}</span>
        <span style="flex:1"></span>
        <label style="font-size:.7rem;color:var(--text2);cursor:pointer"><input type="checkbox" ${k.active?'checked':''} onchange="toggleKnowledge(${k.id}, this.checked)" style="margin-right:4px;accent-color:var(--green)">activo</label>
        <button class="banner-btn" style="padding:4px 8px;font-size:.7rem" onclick="editKnowledge(${k.id})">✎</button>
        <button class="banner-btn danger" style="padding:4px 8px;font-size:.7rem" onclick="deleteKnowledge(${k.id})">🗑</button>
      </div>
      <div style="font-weight:600;font-size:.88rem;margin-bottom:4px">${escapeHtml(k.title)}</div>
      <div style="font-size:.78rem;color:var(--text2);line-height:1.45;white-space:pre-wrap">${escapeHtml(k.content)}</div>
    </div>
  `).join('');
  body.innerHTML = info + (list || '<div style="text-align:center;color:var(--text2);padding:20px">Sin conocimientos cargados</div>');
}
function addKnowledge(){
  openKnowledgeEditor({id:null, category:'general', title:'', content:'', active:1});
}
async function editKnowledge(id){
  const items = await api('/api/knowledge');
  const k = items.find(x => x.id === id);
  if(!k){ alert('No encontrado'); return; }
  openKnowledgeEditor(k);
}
function openKnowledgeEditor(k){
  genModalShow(`
    <div class="gen-modal-box" style="max-width:560px">
      <h3>${k.id ? '✎ Editar' : '+ Nuevo'} conocimiento</h3>
      <label style="display:block;font-size:.65rem;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;font-weight:600">Categoría</label>
      <select id="kbCat" class="gen-modal-input" style="margin-bottom:10px">
        <option value="empresa" ${k.category==='empresa'?'selected':''}>Empresa</option>
        <option value="ubicacion" ${k.category==='ubicacion'?'selected':''}>Ubicación</option>
        <option value="horarios" ${k.category==='horarios'?'selected':''}>Horarios</option>
        <option value="envios" ${k.category==='envios'?'selected':''}>Envíos</option>
        <option value="pagos" ${k.category==='pagos'?'selected':''}>Pagos</option>
        <option value="productos" ${k.category==='productos'?'selected':''}>Productos</option>
        <option value="garantia" ${k.category==='garantia'?'selected':''}>Garantía/devoluciones</option>
        <option value="general" ${k.category==='general'?'selected':''}>General</option>
      </select>
      <label style="display:block;font-size:.65rem;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;font-weight:600">Título corto</label>
      <input id="kbTitle" class="gen-modal-input" value="${escapeHtml(k.title||'')}" placeholder="Ej: Tarifa Pymex 1kg a San José" style="margin-bottom:10px">
      <label style="display:block;font-size:.65rem;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;font-weight:600">Contenido (el bot lo va a leer y usar)</label>
      <textarea id="kbContent" class="gen-modal-input" rows="8" placeholder="Explicale al bot, en lenguaje natural. Ej: 'Para envíos por Pymex a San José y GAM, hasta 1kg cobramos ₡2,500. Para 1-5kg cobramos ₡4,500. Más de 5kg cotizamos.'">${escapeHtml(k.content||'')}</textarea>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="saveKnowledge(${k.id||'null'})">💾 Guardar</button>
      </div>
    </div>`);
  setTimeout(()=>document.getElementById('kbTitle')?.focus(), 50);
}
async function saveKnowledge(id){
  const category = document.getElementById('kbCat').value;
  const title = document.getElementById('kbTitle').value.trim();
  const content = document.getElementById('kbContent').value.trim();
  if(!title || !content){ alert('Título y contenido son obligatorios'); return; }
  try{
    if(id){
      await api('/api/knowledge/'+id, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({category, title, content})});
    } else {
      await api('/api/knowledge', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({category, title, content})});
    }
    genModalClose();
    openKnowledgeDrawer();  // refrescar
  }catch(e){ alert('Error: '+e.message); }
}
async function toggleKnowledge(id, active){
  try{
    await api('/api/knowledge/'+id, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({active})});
    openKnowledgeDrawer();
  }catch(e){ alert('Error: '+e.message); }
}
async function deleteKnowledge(id){
  if(!confirm('¿Borrar este conocimiento?')) return;
  try{
    await api('/api/knowledge/'+id, {method:'DELETE'});
    openKnowledgeDrawer();
  }catch(e){ alert('Error: '+e.message); }
}

// ──── Backups ────
async function openBackupsModal(){
  genModalShow(`
    <div class="gen-modal-box" style="max-width:580px">
      <h3>💾 BACKUPS</h3>
      <p>El backup contiene toda la base de datos (conversaciones, knowledge, sesiones) + las fotos/audios. Se guarda en el VPS, en <code style="background:var(--card);padding:2px 6px;border-radius:3px;font-size:.78rem">/var/backups/whatsapp-bot/</code>.</p>
      <p>Cron automático todos los días a las 3 AM. Mantiene los últimos 30 backups.</p>
      <div class="gen-modal-actions" style="justify-content:flex-start;margin-bottom:14px">
        <button class="banner-btn prim" onclick="runBackupNow()">▶ Hacer backup ahora</button>
      </div>
      <div id="backupsList">Cargando...</div>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cerrar</button>
      </div>
    </div>`);
  loadBackupsList();
}

async function loadBackupsList(){
  const el = document.getElementById('backupsList');
  if(!el) return;
  try{
    const r = await api('/api/backups');
    const list = r.backups || [];
    if(!list.length){
      el.innerHTML = '<div style="color:var(--text2);padding:14px;text-align:center;border:1px dashed var(--border2);border-radius:6px">No hay backups aún. Pulsá "Hacer backup ahora" para crear el primero.</div>';
      return;
    }
    el.innerHTML = `
      <div style="font-size:.7rem;color:var(--text2);letter-spacing:.4px;text-transform:uppercase;margin-bottom:6px;font-weight:600">Últimos backups · ${list.length} archivos</div>
      <div style="max-height:300px;overflow-y:auto">
        ${list.map(b => {
          const d = new Date(b.modified*1000).toLocaleString('es-CR',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
          return `<div style="display:flex;gap:10px;align-items:center;padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;background:var(--card)">
            <div style="flex:1;min-width:0">
              <div style="font-weight:600;font-size:.85rem">${escapeHtml(b.filename)}</div>
              <div style="font-size:.72rem;color:var(--text2);margin-top:2px">${d} · ${escapeHtml(b.size_human)}</div>
            </div>
            <a class="banner-btn" href="/api/backups/${encodeURIComponent(b.filename)}" download style="padding:5px 10px;font-size:.72rem">📥 Descargar</a>
          </div>`;
        }).join('')}
      </div>`;
  }catch(e){
    el.innerHTML = `<div style="color:var(--red);padding:10px">Error: ${e.message}</div>`;
  }
}

async function runBackupNow(){
  const el = document.getElementById('backupsList');
  if(el) el.innerHTML = '<div style="text-align:center;color:var(--text2);padding:20px">Generando backup... esto puede tardar varios segundos.</div>';
  try{
    const r = await api('/api/backups/run-now', {method:'POST'});
    if(r?.ok){
      alert(`✓ Backup creado: ${r.filename} (${r.size_human}) en ${r.duration_s}s`);
      loadBackupsList();
    } else {
      alert('Error: ' + (r?.error||''));
      loadBackupsList();
    }
  }catch(e){
    alert('Error: ' + e.message);
    loadBackupsList();
  }
}

async function markStatus(newStatus){
  if(!CURRENT_PHONE) return;
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/status`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({status: newStatus})
    });
    if(r?.ok){
      if(CURRENT_INFO) CURRENT_INFO.status = newStatus;
      renderStatusBadge();
      renderActions();
      loadConvs();
      loadStats();
    }
  }catch(e){ alert('Error: ' + e.message); }
}

async function confirmOrderInOdoo(){
  if(!CURRENT_PHONE || !CURRENT_INFO?.odoo_sale_order_id) return;
  if(!confirm('¿Confirmar la cotización ' + (CURRENT_INFO.odoo_sale_order_name||'') + ' en Odoo? Esto crea el picking de salida.')) return;
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/confirm-order`, {method:'POST'});
    if(r?.ok){
      alert('✓ Confirmado: ' + (r.order_name||'') + ' → picking ' + (r.picking_name||''));
      markStatus('a_despachar');
    } else {
      alert('Error: ' + (r?.error || 'desconocido'));
    }
  }catch(e){ alert('Error: ' + e.message); }
}

function renderPartner(p){
  const el = document.getElementById('partnerInfo');
  if(!p){
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  const isClient = p.sale_count > 0;
  const badge = isClient
    ? `<span style="background:rgba(76,175,110,.15); color:#5cd684; padding:1px 6px; border-radius:8px; font-size:.55rem; font-weight:700">CLIENTE · ${p.sale_count} pedidos</span>`
    : `<span style="background:rgba(232,168,0,.15); color:#fbbf24; padding:1px 6px; border-radius:8px; font-size:.55rem; font-weight:700">NUEVO</span>`;
  const ciudad = p.city ? ' · ' + escapeHtml(p.city) : '';
  const mail = p.email ? ' · ' + escapeHtml(p.email) : '';
  el.innerHTML = `${badge} <a href="${p.url}" target="_blank" style="color:#7eb1ff; text-decoration:none">Odoo #${p.id}</a>${ciudad}${mail}`;
  el.style.display = 'block';
}

function closeChat(){
  document.getElementById('app').classList.remove('show-chat');
  CURRENT_PHONE = null;
  updateMobilePill();
}

async function toggleEscalate(){
  if(!CURRENT_PHONE) return;
  const newVal = !CURRENT_INFO?.escalated;
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/escalate`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({escalated: newVal})
    });
    if(r){ CURRENT_INFO.escalated = r.escalated; renderActions(); loadStats(); loadConvs(); }
  }catch(e){ alert('Error: '+e.message); }
}

// Firma simple del último estado renderizado, para evitar re-render si nada cambió.
let LAST_MSGS_SIG = '';

function renderMessages(msgs, opts){
  opts = opts || {};
  const body = document.getElementById('chatBody');
  if(!msgs.length){
    body.innerHTML = '<div class="empty"><span class="emoji" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg></span><h3>Sin mensajes</h3></div>';
    LAST_MSGS_SIG = '';
    return;
  }
  // Firma: cantidad + último id + último status + último ts. Si no cambió, no tocamos el DOM
  // (eso evita que el polling cada 8s interrumpa la lectura y borre la selección de texto).
  const last = msgs[msgs.length - 1] || {};
  const sig = msgs.length + '|' + (last.wa_msg_id || last.ts || '') + '|' + (last.status || '') + '|' + (last.text||'').length;
  if(!opts.force && sig === LAST_MSGS_SIG){
    return;
  }
  // Si el usuario tiene texto seleccionado dentro del chat, no re-renderizamos
  // (perdería su selección). Esperamos al siguiente tick del polling.
  if(!opts.force){
    const sel = window.getSelection && window.getSelection();
    if(sel && !sel.isCollapsed && sel.anchorNode && body.contains(sel.anchorNode)){
      return;
    }
  }
  // Capturamos si el usuario estaba leyendo arriba o pegado al fondo, para decidir
  // si scrollear al fondo (mensaje nuevo) o mantener su posición exacta.
  const distFromBottom = body.scrollHeight - body.scrollTop - body.clientHeight;
  const wasAtBottom = distFromBottom < 100;
  const prevScrollTop = body.scrollTop;
  const prevScrollHeight = body.scrollHeight;

  body.innerHTML = msgs.map(m => {
    const time = new Date(m.ts*1000).toLocaleTimeString('es-CR',{hour:'2-digit',minute:'2-digit'});
    const cls = m.direction === 'in' ? 'in' : (m.bot_replied ? 'out bot' : 'out');
    let tick = '';
    if(m.direction === 'out' && m.wa_msg_id){
      const st = (m.status || 'sent').toLowerCase();
      if(st === 'failed') tick = ' <span title="No entregado" style="color:#d33;font-weight:700">!</span>';
      else if(st === 'read') tick = ' <span title="Leído" style="color:#53bdeb;font-weight:700;letter-spacing:-2px">✓✓</span>';
      else if(st === 'delivered') tick = ' <span title="Entregado" style="color:rgba(0,0,0,.45);font-weight:700;letter-spacing:-2px">✓✓</span>';
      else tick = ' <span title="Enviado" style="color:rgba(0,0,0,.45);font-weight:700">✓</span>';
    }
    const meta = (m.direction === 'out' && m.bot_replied ? '🤖 ' + time : time) + tick;
    let bubble = '';
    if(m.media_path){
      const isAudio = /\.(ogg|oga|mp3|m4a|mp4|wav)$/i.test(m.media_path);
      if(isAudio){
        const transcript = (m.text||'').replace(/^🎙️\s*/,'').replace(/^\[AUDIO\][^a-zA-Z0-9]*/,'');
        bubble = `<div class="bubble" style="padding:8px 10px">
          <audio controls preload="none" src="/media/${encodeURIComponent(m.media_path)}" style="width:240px;max-width:100%;display:block;margin-bottom:6px"></audio>
          <div style="font-size:.7rem; opacity:.85; line-height:1.35">🎙️ ${escapeHtml(transcript)}</div>
        </div>`;
      } else {
        bubble = `<div class="bubble" style="padding:6px"><img src="/media/${encodeURIComponent(m.media_path)}" style="max-width:240px; max-height:300px; border-radius:10px; display:block" alt="foto" loading="lazy"><div style="padding:4px 6px 2px; font-size:.7rem; opacity:.85">${escapeHtml(m.text||'').replace(/^\[FOTO\]\s*/,'')}</div></div>`;
      }
    } else {
      bubble = `<div class="bubble">${escapeHtml(m.text||'')}</div>`;
    }
    return `<div class="msg ${cls}"><div>${bubble}<div class="bubble-meta">${meta}</div></div></div>`;
  }).join('');
  LAST_MSGS_SIG = sig;
  if(opts.force || wasAtBottom){
    body.scrollTop = body.scrollHeight;
  } else {
    // Mantener posición visual: ajustar por delta de altura por si entraron mensajes arriba.
    body.scrollTop = prevScrollTop + (body.scrollHeight - prevScrollHeight);
  }
}

function escapeHtml(s){
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function sendReply(){
  const ta = document.getElementById('replyText');
  const text = ta.value.trim();
  if(!CURRENT_PHONE) return;
  if(!text && !PENDING_IMAGE) return;
  const btn = document.getElementById('sendBtn');
  btn.disabled = true;
  try{
    let r;
    if(PENDING_IMAGE){
      const fd = new FormData();
      fd.append('image', PENDING_IMAGE.blob, PENDING_IMAGE.name);
      if(text) fd.append('caption', text);
      const resp = await fetch(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/reply-image`, {
        method:'POST', credentials:'same-origin', body: fd
      });
      if(resp.status === 401){ location.reload(); return; }
      r = await resp.json().catch(() => ({ok:false, error:'respuesta inválida'}));
    } else {
      r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/reply`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({text})
      });
    }
    if(r?.ok){
      ta.value = '';
      ta.style.height = 'auto';
      clearPendingImage();
      openConv(CURRENT_PHONE);  // recarga
    } else {
      alert('Error: ' + (r?.error || 'desconocido'));
    }
  }catch(e){ alert('Error: '+e.message); }
  finally{ btn.disabled = false; }
}

function onReplyKey(e){
  // Enter = enviar (como WhatsApp). Shift+Enter = salto de línea.
  if(e.key === 'Enter' && !e.shiftKey && !e.isComposing){
    e.preventDefault();
    sendReply();
    return;
  }
  // Auto-resize textarea
  setTimeout(() => {
    const ta = e.target;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
  }, 0);
}

// ───── Pegar / adjuntar imágenes ─────
let PENDING_IMAGE = null; // {blob, name, mime, dataUrl}

function onReplyPaste(e){
  const items = (e.clipboardData || window.clipboardData)?.items || [];
  for(const it of items){
    if(it.kind === 'file' && it.type.startsWith('image/')){
      e.preventDefault();
      const blob = it.getAsFile();
      if(blob) showImagePreview(blob);
      return;
    }
  }
}

function onImgFileChosen(e){
  const f = e.target.files && e.target.files[0];
  if(f) showImagePreview(f);
  e.target.value = ''; // reset
}

function showImagePreview(blob){
  const reader = new FileReader();
  reader.onload = ev => {
    PENDING_IMAGE = {blob, name: blob.name || 'imagen.jpg', mime: blob.type || 'image/jpeg', dataUrl: ev.target.result};
    let box = document.getElementById('imgPreviewBox');
    if(!box){
      box = document.createElement('div');
      box.id = 'imgPreviewBox';
      box.style.cssText = 'position:absolute;bottom:60px;left:10px;right:10px;background:#fff;border:1px solid var(--border);border-radius:8px;padding:8px;display:flex;gap:10px;align-items:center;box-shadow:0 2px 8px rgba(0,0,0,.15);z-index:50';
      const composer = document.querySelector('.composer') || document.getElementById('replyText').parentElement;
      composer.style.position = 'relative';
      composer.appendChild(box);
    }
    box.innerHTML = `
      <img src="${ev.target.result}" style="width:60px;height:60px;object-fit:cover;border-radius:6px">
      <div style="flex:1;font-size:.85rem;color:var(--text)">Imagen lista para enviar<br><span style="font-size:.7rem;color:var(--text2)">Escribí un caption (opcional) y dale Enter o ➤</span></div>
      <button onclick="clearPendingImage()" style="background:transparent;border:none;font-size:1.1rem;cursor:pointer;color:var(--text2)" title="Descartar">✕</button>
    `;
  };
  reader.readAsDataURL(blob);
}

function clearPendingImage(){
  PENDING_IMAGE = null;
  const box = document.getElementById('imgPreviewBox');
  if(box) box.remove();
}

async function doLogout(){
  await fetch('/logout', {method:'POST', credentials:'same-origin'});
  location.reload();
}

// Menú ⋮ del topbar (solo móvil) — toggle + cerrar al click fuera
function toggleTopbarMenu(ev){
  if(ev) ev.stopPropagation();
  const m = document.getElementById('topbarMenu');
  if(!m) return;
  m.classList.toggle('open');
}
function closeTopbarMenu(){
  const m = document.getElementById('topbarMenu');
  if(m) m.classList.remove('open');
}
document.addEventListener('click', (e) => {
  const m = document.getElementById('topbarMenu');
  if(!m || !m.classList.contains('open')) return;
  // Si el click fue fuera del menú y fuera del botón toggle, cerrar
  if(!m.contains(e.target) && !e.target.closest('.topbar-menu-toggle')){
    m.classList.remove('open');
  }
});

// Polling cada 8s para refrescar
function startPolling(){
  if(POLL_TIMER) clearInterval(POLL_TIMER);
  POLL_TIMER = setInterval(() => {
    loadStats();
    loadConvs();
    if(CURRENT_PHONE){
      api('/api/conversation/' + encodeURIComponent(CURRENT_PHONE))
        .then(d => { if(d){ renderMessages(d.messages || []); CURRENT_INFO = d.info; CURRENT_PARTNER = d.partner; renderStatusBadge(); renderActions(); }})
        .catch(()=>{});
    }
  }, 8000);
}

// Diagnóstico visible: si hay error JS, mostramos un banner rojo arriba del panel.
// Así no hace falta abrir DevTools para detectar bugs que rompen el polling.
(function(){
  function showErr(msg, src){
    let b = document.getElementById('jsErrBanner');
    if(!b){
      b = document.createElement('div');
      b.id = 'jsErrBanner';
      b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#c53030;color:#fff;padding:8px 14px;font:600 .75rem/1.3 monospace;text-align:center;box-shadow:0 2px 6px rgba(0,0,0,.3);max-height:120px;overflow:auto';
      document.body.appendChild(b);
    }
    const line = '[JS error] ' + msg + (src ? ' · ' + src : '') + ' · ' + new Date().toLocaleTimeString();
    const NL = String.fromCharCode(10);
    b.innerText = (line + NL + (b.innerText || '')).split(NL).slice(0,5).join(NL);
  }
  window.addEventListener('error', e => showErr(e.message, e.filename ? (e.filename + ':' + e.lineno) : ''));
  window.addEventListener('unhandledrejection', e => showErr('Promise: ' + (e.reason && (e.reason.message || e.reason)), ''));
})();

loadStats();
loadConvs();
startPolling();

// ───────── Meta chip (termómetro siempre visible) ─────────
let META_CHIP_TIMER = null;
async function updateMetaChip(){
  const chip = document.getElementById('metaChip');
  if(!chip) return;
  try{
    const [d, modeInfo] = await Promise.all([
      api('/api/meta/health'),
      api('/api/bot/mode'),
    ]);
    if(!d || !d.ok){ chip.className = 'meta-chip loading'; chip.querySelector('.meta-label').textContent = 'Cuenta'; return; }
    const lvl = (d.phone && d.phone.quality && d.phone.quality.level) || 'muted';
    const raw = (d.phone && d.phone.quality && d.phone.quality.raw) || '—';
    const tier = (d.phone && d.phone.throughput) || '';
    const conv7 = (d.analytics_7d && d.analytics_7d.total) || 0;
    const mode = (modeInfo && modeInfo.mode) || 'normal';
    // Base: nivel de quality
    let cls = 'meta-chip ' + (lvl === 'ok' ? 'ok' : (lvl === 'warn' ? 'warn' : (lvl === 'err' ? 'err' : 'loading')));
    // Modificador: si el modo del bot está alterado lo añadimos como clase
    if(mode !== 'normal') cls += ' mode-' + mode;
    chip.className = cls;
    // Texto siempre "Cuenta", el dot adyacente comunica el modo
    chip.querySelector('.meta-label').textContent = 'Cuenta';
    const modeStr = mode === 'normal' ? 'modo normal' : (mode === 'conservative' ? 'modo CONSERVADOR' : 'modo SOLO HUMANO');
    chip.title = `Cuenta WhatsApp
Quality: ${raw}${tier ? ' · ' + tier : ''}
${conv7} conversaciones últimos 7 días
Bot: ${modeStr}
— Click para detalle y control —`;
  }catch(e){
    chip.className = 'meta-chip loading';
    chip.querySelector('.meta-label').textContent = 'Cuenta';
  }
}
function startMetaChipPolling(){
  if(META_CHIP_TIMER) clearInterval(META_CHIP_TIMER);
  updateMetaChip();
  META_CHIP_TIMER = setInterval(updateMetaChip, 5 * 60 * 1000); // 5 min (alineado con cache backend)
}
startMetaChipPolling();

// ───────── PWA: Service Worker + Web Push ─────────
let SW_REG = null;

function urlBase64ToUint8Array(base64String){
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for(let i=0;i<raw.length;i++) arr[i]=raw.charCodeAt(i);
  return arr;
}

async function registerSW(){
  if(!('serviceWorker' in navigator)) return null;
  try{
    const reg = await navigator.serviceWorker.register('/sw.js', {scope:'/'});
    SW_REG = reg;
    // Listener: SW pide abrir un chat al hacer click en una notificación
    navigator.serviceWorker.addEventListener('message', (ev) => {
      const d = ev.data || {};
      if(d.type === 'open-chat' && d.phone){
        try{ openConv(d.phone); }catch(_){}
      }
    });
    return reg;
  }catch(e){
    console.warn('[sw] register failed', e);
    return null;
  }
}

async function refreshNotifButton(){
  const btn = document.getElementById('notifBtn');
  const menuBtn = document.getElementById('menuNotifBtn');
  if(!btn) return;
  if(!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)){
    btn.style.display = 'none';
    if(menuBtn) menuBtn.style.display = 'none';
    return;
  }
  btn.style.display = 'inline-flex';
  if(menuBtn) menuBtn.style.display = 'flex';
  const reg = SW_REG || await navigator.serviceWorker.getRegistration();
  let subbed = false;
  if(reg){
    try{ const sub = await reg.pushManager.getSubscription(); subbed = !!sub; }catch(_){}
  }
  const perm = Notification.permission;
  const targets = [btn, menuBtn].filter(Boolean);
  let title;
  if(subbed && perm === 'granted'){
    title = 'Notificaciones activadas (click para desactivar)';
    targets.forEach(el => { el.classList.add('subscribed'); el.classList.remove('blocked'); });
  }else if(perm === 'denied'){
    title = 'Notificaciones bloqueadas — habilitalas en ajustes del navegador';
    targets.forEach(el => { el.classList.remove('subscribed'); el.classList.add('blocked'); });
  }else{
    title = 'Activar notificaciones del panel';
    targets.forEach(el => { el.classList.remove('subscribed'); el.classList.remove('blocked'); });
  }
  targets.forEach(el => { el.title = title; });
}

async function togglePushSubscription(){
  if(!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)){
    alert('Tu navegador no soporta notificaciones push.');
    return;
  }
  const reg = SW_REG || await navigator.serviceWorker.getRegistration() || await registerSW();
  if(!reg){ alert('No se pudo registrar el service worker.'); return; }

  const existing = await reg.pushManager.getSubscription();
  if(existing){
    // Desactivar
    try{
      await fetch('/api/push/unsubscribe', {
        method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({endpoint: existing.endpoint}),
      });
      await existing.unsubscribe();
    }catch(e){ console.warn(e); }
    refreshNotifButton();
    return;
  }

  if(Notification.permission === 'denied'){
    alert('Las notificaciones están bloqueadas. Habilitalas en los ajustes del navegador y volvé a intentar.');
    return;
  }
  const perm = await Notification.requestPermission();
  if(perm !== 'granted'){ refreshNotifButton(); return; }

  // Pedir VAPID y suscribir
  try{
    const r = await fetch('/api/push/vapid-key', {credentials:'same-origin'});
    if(!r.ok){ alert('VAPID no configurado en el servidor.'); return; }
    const {key} = await r.json();
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    });
    const subJson = sub.toJSON();
    await fetch('/api/push/subscribe', {
      method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        endpoint: sub.endpoint,
        keys: subJson.keys,
        ua: navigator.userAgent,
      }),
    });
    refreshNotifButton();
    // Push de prueba opcional
    fetch('/api/push/test', {method:'POST', credentials:'same-origin'}).catch(()=>{});
  }catch(e){
    console.warn('[push subscribe] error', e);
    alert('No se pudo activar las notificaciones: ' + (e.message || e));
  }
}

// Arrancar PWA en background
registerSW().then(() => refreshNotifButton());