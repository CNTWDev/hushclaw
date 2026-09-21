import { uiText, localeTag } from "../i18n.js";
/** Single-gateway account centre. Credentials never enter the browser. */
import { wizard, els, send, escHtml } from '../state.js';

export const vox = {
  authed: false, pending: false, account: null, models: [], packages: null,
  error: '', note: '', authorizeUrl: '', order: null, checkoutUrl: '',
  deploymentReady: false,
};
let sequence = 0;
let generation = 0;
const requests = new Map();
let pollTimer;
let orderChecks = 0;

export function setVoxConfig(config = {}) {
  vox.deploymentReady = Boolean(config.gateway && config.client_id);
  wizard.baseUrl = config.gateway || '';
  wizard.voxIssuer = config.issuer || 'https://auth.voxnexus.ai';
  wizard.voxClientId = config.client_id || '';
}

export function voxReady() {
  return vox.authed && vox.account?.status === 'active' && Number(vox.account?.available) > 0;
}

export function resetVoxRequests() {
  generation++;
  for (const value of requests.values()) clearTimeout(value.timer);
  requests.clear();
  clearTimeout(pollTimer);
  vox.authed = false;
  vox.pending = false;
  vox.account = null;
  vox.models = [];
  vox.packages = null;
  paint();
}

function request(action, extra = {}) {
  if ([...requests.values()].some(r => r.action === action)) return;
  const request_id = `vox-${generation}-${++sequence}`;
  const timer = setTimeout(() => {
    requests.delete(request_id);
    vox.error = action === 'topup' ? uiText("Order outcome unconfirmed. Check payments before placing another order.") : uiText("Request timed out. Refresh and try again.");
    paint();
  }, 60000);
  requests.set(request_id, { action, timer });
  send({ type: 'voxnexus', action, request_id, ...extra });
}

function poll(action, extra = {}) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => {
    if (wizard.open && wizard.tab === 'model') request(action, extra);
  }, action === 'order' ? 4000 : 2000);
}

function refresh() {
  vox.error = '';
  request('status');
}

function number(value) { return Number.isFinite(Number(value)) ? Number(value).toLocaleString(localeTag()) : '—'; }
function safeLink(value, stripe = false) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && (!stripe || url.hostname === 'checkout.stripe.com') ? url.href : '';
  } catch { return ''; }
}

function metadata() {
  const model = vox.models.find(m => m.id === wizard.model);
  if (!model) return '';
  const tags = Array.isArray(model.tags) ? model.tags.join(' · ') : '';
  return `<div class="vox-model-meta"><span>${uiText("Context window")} ${number(model.context_window)}</span><span>${uiText("Max output")} ${number(model.max_output_tokens)}</span>${tags ? `<span>${escHtml(tags)}</span>` : ''}</div>
    ${model.pricing && typeof model.pricing === 'object' ? `<div class="vox-model-meta">${Object.entries(model.pricing).map(([k, v]) => `<span>${escHtml(k)} × ${escHtml(String(v))}</span>`).join('')}</div>` : ''}`;
}

function paint() {
  if (wizard.open && wizard.tab === 'model') renderModelTab(false);
}

export function renderModelTab(load = true) {
  wizard.provider = 'voxnexus';
  const ready = voxReady();
  const account = vox.account;
  const options = vox.models.map(m => `<option value="${escHtml(m.id)}" ${wizard.model === m.id ? 'selected' : ''}>${escHtml(m.id)}</option>`).join('');
  const packages = vox.packages?.payments_enabled ? (vox.packages.data || []) : [];
  const busy = [...requests.values()].some(r => ['login', 'logout', 'topup'].includes(r.action));
  const orderPending = vox.order?.status === 'pending';
  els.wizardBody.innerHTML = `
    <section class="vox-profile settings-section">
      <div class="vox-profile-head"><div class="vox-avatar" aria-hidden="true">V</div><div><h2>VoxNexus</h2><p>${escHtml(account?.email || (vox.authed ? uiText("Signed in") : uiText("Account centre")))}</p></div><span class="vox-badge">${vox.pending ? uiText("Waiting for browser authorization") : vox.authed ? uiText("Signed in") : uiText("Signed out")}</span></div>
      <p class="wdesc" data-i18n="ui:One account for models, credits and top-ups.">一个账号，管理模型、额度与充值。</p>
      ${!vox.deploymentReady ? '<p class="vox-message" data-i18n="ui:Waiting for VoxNexus configuration. If this persists, restart HushClaw and refresh the page.">正在等待服务端加载 VoxNexus 登录配置。如持续出现，请重启 HushClaw 服务并刷新页面。</p>' : ''}
      <div class="vox-actions">
        ${!vox.authed ? `<button type="button" id="vox-login" ${!vox.deploymentReady || vox.pending || busy ? 'disabled' : ''} data-i18n="ui:Sign in to VoxNexus">登录 VoxNexus</button>` : ''}
        ${vox.authed || vox.pending ? `<button type="button" id="vox-logout" class="secondary" ${busy ? 'disabled' : ''}>${vox.pending ? uiText("Cancel sign-in") : uiText("Sign out")}</button>` : ''}
        <button type="button" id="vox-refresh" class="secondary" ${!vox.deploymentReady ? 'disabled' : ''} data-i18n="ui:Refresh">刷新</button>
      </div>
      ${vox.pending && safeLink(vox.authorizeUrl) ? `<p class="wfield-hint">请在系统浏览器完成登录。<a href="${escHtml(safeLink(vox.authorizeUrl))}" target="_blank" rel="noopener noreferrer" data-i18n="ui:Reopen sign-in page">重新打开登录页</a></p>` : ''}
      <div aria-live="polite">${vox.error ? `<p class="vox-message vox-error">${escHtml(vox.error)}</p>` : ''}${vox.note ? `<p class="vox-message">${escHtml(uiText(vox.note))}</p>` : ''}</div>
    </section>
    <section class="settings-section"><h3 class="settings-section-h" data-i18n="ui:My credits">我的额度</h3>
      <div class="vox-balance"><div><span data-i18n="ui:Available credits">可用额度</span><strong>${vox.authed && account ? number(account.available) : '—'}</strong><small data-i18n="ui:Credit units">额度单位</small></div><div><span data-i18n="ui:Reserved">请求中冻结</span><strong>${vox.authed && account ? number(account.reserved) : '—'}</strong><small data-i18n="ui:Settled after requests complete">请求结束后结算</small></div></div>
      <p class="wfield-hint">${!vox.authed ? uiText("Sign in to view credits and packages.") : account && !ready ? uiText("No available credits or the account is unavailable. Choose a model once credits arrive.") : uiText("Requests are charged by model rate and actual usage.")}</p>
      ${packages.length ? `<div class="vox-packages">${packages.map(p => `<button type="button" class="secondary vox-package" data-package="${escHtml(p.id)}" ${busy || orderPending ? 'disabled' : ''}><strong>${escHtml(p.name)}</strong><span>${number(p.tokens)} ${uiText("credits")}</span><span>${escHtml(String(p.currency).toUpperCase())} ${(Number(p.amount_cents) / 100).toFixed(2)}</span></button>`).join('')}</div>` : ''}
      ${vox.authed && vox.packages?.payments_enabled === false ? '<p class="wfield-hint" data-i18n="ui:Online top-ups are unavailable on this gateway.">当前网关未开放在线充值。</p>' : ''}
      ${vox.order ? `<div class="vox-order"><p>${uiText("Order")} ${escHtml(vox.order.id || '')} · ${escHtml(vox.order.status || 'pending')}</p>${safeLink(vox.checkoutUrl, true) && orderPending ? `<a href="${escHtml(safeLink(vox.checkoutUrl, true))}" target="_blank" rel="noopener noreferrer" data-i18n="ui:Open checkout">打开支付页面</a>` : ''}<button type="button" id="vox-check-order" class="secondary" data-i18n="ui:Check payment status">查询到账状态</button></div>` : ''}
    </section>
    <section class="settings-section"><h3 class="settings-section-h" data-i18n="ui:Model settings">模型配置</h3>
      <p class="wdesc">${ready ? uiText("Choose the main and background models, then save to apply.") : uiText("Sign in with available credits to choose and use models.")}</p>
      <div class="wfield"><label for="wiz-model-select" data-i18n="ui:Main model">主模型</label><select id="wiz-model-select" ${ready && vox.models.length ? '' : 'disabled'}><option value="" data-i18n="ui:Choose a model">请选择模型</option>${options}</select></div>
      ${ready ? metadata() : ''}
      <div class="wfield"><label for="sys-cheap-model" data-i18n="ui:Background model">后台任务模型</label><select id="sys-cheap-model" ${ready && vox.models.length ? '' : 'disabled'}><option value="" data-i18n="ui:Use main model">跟随主模型</option>${vox.models.map(m => `<option value="${escHtml(m.id)}" ${wizard.cheapModel === m.id ? 'selected' : ''}>${escHtml(m.id)}</option>`).join('')}</select></div>
    </section>`;
  document.getElementById('vox-login')?.addEventListener('click', () => {
    vox.error = ''; vox.note = ''; vox.pending = true;
    request('login'); paint();
  });
  document.getElementById('vox-logout')?.addEventListener('click', () => {
    resetVoxRequests(); vox.order = null; vox.checkoutUrl = ''; vox.authorizeUrl = ''; vox.note = '';
    request('logout'); paint();
  });
  document.getElementById('vox-refresh')?.addEventListener('click', refresh);
  document.getElementById('wiz-model-select')?.addEventListener('change', e => { wizard.model = e.target.value; paint(); });
  document.getElementById('sys-cheap-model')?.addEventListener('change', e => { wizard.cheapModel = e.target.value; });
  els.wizardBody.querySelectorAll('[data-package]').forEach(button => button.addEventListener('click', () => {
    vox.error = ''; request('topup', { package_id: button.dataset.package }); paint();
  }));
  document.getElementById('vox-check-order')?.addEventListener('click', () => {
    orderChecks = 0; request('order', { order_id: vox.order.id });
  });
  if (load && vox.deploymentReady) refresh();
}

export function handleVoxResult(msg) {
  const pending = requests.get(msg.request_id);
  if (!pending || pending.action !== msg.action) return;
  clearTimeout(pending.timer);
  requests.delete(msg.request_id);
  if (!msg.ok) {
    vox.error = msg.error || uiText("Request failed. Please try again.");
    if (msg.action === 'login') vox.pending = false;
    if (msg.action === 'account') vox.account = null;
    if (msg.status === 401) resetVoxRequests();
    paint(); return;
  }
  if (msg.action === 'login') {
    vox.authorizeUrl = msg.authorize_url || '';
    poll('status');
  } else if (msg.action === 'logout') {
    vox.note = uiText("Signed out. Local tokens cleared.");
  } else if (msg.action === 'status') {
    vox.authed = Boolean(msg.authed);
    vox.pending = Boolean(msg.pending);
    if (msg.login_error) vox.error = msg.login_error;
    if (vox.pending) poll('status');
    else if (vox.authed) {
      vox.authorizeUrl = '';
      request('account'); request('packages');
      if (vox.order?.status === 'pending') poll('order', { order_id: vox.order.id });
    } else { vox.account = null; vox.models = []; vox.packages = null; }
  } else if (msg.action === 'account') {
    vox.account = msg.account;
    if (voxReady()) request('models');
    else vox.models = [];
  } else if (msg.action === 'models') {
    vox.models = (msg.models || []).filter(m => typeof m.id === 'string');
    if (!vox.models.some(m => m.id === wizard.model)) wizard.model = '';
    if (!vox.models.some(m => m.id === wizard.cheapModel)) wizard.cheapModel = '';
  } else if (msg.action === 'packages') {
    vox.packages = msg.packages;
  } else if (msg.action === 'topup' || msg.action === 'order') {
    vox.order = msg.order?.topup || msg.order;
    if (msg.action === 'topup') { vox.checkoutUrl = msg.order?.checkout_url || ''; orderChecks = 0; }
    if (vox.order?.status === 'paid') {
      vox.note = uiText("Top-up received. Refreshing credits\u2026"); request('account');
    } else if (vox.order?.status === 'pending' && orderChecks++ < 75) {
      vox.note = uiText("Waiting for payment confirmation; credits will update automatically.");
      poll('order', { order_id: vox.order.id });
    } else {
      vox.note = vox.order?.status === 'pending' ? uiText("The order is still processing. Check payment status later.") : uiText("The order has ended. You can select a package to top up again.");
    }
  }
  paint();
}

document.addEventListener("locale-changed", paint);
