/**
 * panels/enterprise.js — Enterprise platform and domain substrate panel.
 */

import { enterpriseState, escHtml, send } from "../state.js";
import { renderLoadingMarkup } from "../loading.js";

function _contentEl() {
  return document.getElementById("enterprise-content");
}

function _count(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

function _statusPill(text, tone = "neutral") {
  return `<span class="enterprise-pill ${tone}">${escHtml(text)}</span>`;
}

function _renderPlatformCard(title, text, tone = "") {
  return `
    <div class="enterprise-foundation-card ${tone}">
      <div class="enterprise-foundation-mark"></div>
      <div>
        <strong>${escHtml(title)}</strong>
        <span>${escHtml(text)}</span>
      </div>
    </div>
  `;
}

function _renderDomainCard(item) {
  const manifest = item.manifest || {};
  const status = item.status || {};
  const enabled = !!status.enabled;
  const planned = manifest.status === "planned";
  const tone = enabled ? "ok" : planned ? "planned" : "neutral";
  const caps = manifest.capabilities || [];
  const entities = manifest.entity_types || [];
  return `
    <button class="enterprise-domain-card ${tone}" data-enterprise-domain="${escHtml(manifest.id || "")}">
      <div class="enterprise-domain-top">
        <span class="enterprise-domain-icon">${escHtml((manifest.name || "?").slice(0, 2).toUpperCase())}</span>
        <div>
          <span class="enterprise-card-kicker">Business Domain</span>
          <strong>${escHtml(manifest.name || manifest.id || "Domain")}</strong>
        </div>
        ${_statusPill(enabled ? "Enabled" : planned ? "Planned" : "Available", tone)}
      </div>
      <p>${escHtml(manifest.description || "")}</p>
      <div class="enterprise-chip-row">
        ${caps.slice(0, 4).map((cap) => `<span>${escHtml(cap)}</span>`).join("")}
      </div>
      <div class="enterprise-domain-meta">
        <span>${_count(entities.length)} entity types</span>
        <span>${_count((manifest.tools || []).length)} tools</span>
        <span>${_count((manifest.agents || []).length)} agents</span>
      </div>
    </button>
  `;
}

function _renderMembers() {
  const members = enterpriseState.members || [];
  if (!members.length) {
    return `<div class="enterprise-empty-row">No members loaded.</div>`;
  }
  return members.map((m) => `
    <div class="enterprise-member-row">
      <div>
        <strong>${escHtml(m.display_name || m.id || "Member")}</strong>
        <span>${escHtml(m.email || m.title || "")}</span>
      </div>
      <span>${escHtml(m.unit_id || "org")}</span>
      ${_statusPill(m.status || "active", "ok")}
    </div>
  `).join("");
}

function _renderRoles() {
  const roles = enterpriseState.roles || [];
  if (!roles.length) return `<div class="enterprise-empty-row">No roles loaded.</div>`;
  return roles.map((role) => `
    <div class="enterprise-role-row">
      <div>
        <strong>${escHtml(role.name || role.id)}</strong>
        <span>${escHtml(role.description || "")}</span>
      </div>
      <div class="enterprise-chip-row">
        ${(role.permissions || []).slice(0, 4).map((p) => `<span>${escHtml(p)}</span>`).join("")}
      </div>
    </div>
  `).join("");
}

export function refreshEnterprisePanel() {
  send({ type: "enterprise_get_overview" });
  send({ type: "enterprise_list_members" });
  send({ type: "enterprise_list_org_units" });
  send({ type: "enterprise_list_roles" });
  send({ type: "os_list_domains" });
}

export function renderEnterprisePanel() {
  const el = _contentEl();
  if (!el) return;
  const overview = enterpriseState.overview;
  if (!overview) {
    el.innerHTML = renderLoadingMarkup({ status: "Loading enterprise platform…", height: 180 });
    return;
  }

  const distro = overview.distro || {};
  const directory = overview.directory || {};
  const counts = directory.counts || {};
  const domains = enterpriseState.domains || [];
  const foundation = overview.platform?.foundation || [];
  const org = directory.org || {};

  el.innerHTML = `
    <section class="enterprise-hero">
      <div>
        <span class="enterprise-eyebrow">Enterprise Distro</span>
        <h2>${escHtml(org.name || distro.name || "Enterprise Platform")}</h2>
        <p>AgentOS manages identity, policy, memory, audit, and domain lifecycle while business domains provide their own semantics.</p>
      </div>
      <div class="enterprise-hero-stats">
        <div><strong>${escHtml(distro.id || "personal")}</strong><span>Distro</span></div>
        <div><strong>${_count(counts.members)}</strong><span>Members</span></div>
        <div><strong>${_count(domains.length)}</strong><span>Domains</span></div>
        <div><strong>${_count(counts.roles)}</strong><span>Roles</span></div>
      </div>
    </section>

    <section class="enterprise-section">
      <div class="enterprise-section-head">
        <div>
          <span class="enterprise-eyebrow">Foundation</span>
          <h3>Base Platform</h3>
        </div>
        ${_statusPill("Read-only v1", "planned")}
      </div>
      <div class="enterprise-foundation-grid">
        ${foundation.map((name) => _renderPlatformCard(name, name === "Domain Registry" ? "Business packages register through contracts." : "Enterprise substrate capability.")).join("")}
      </div>
    </section>

    <section class="enterprise-section enterprise-split">
      <div>
        <div class="enterprise-section-head">
          <div>
            <span class="enterprise-eyebrow">Directory</span>
            <h3>Members</h3>
          </div>
          <button class="secondary enterprise-refresh-btn" data-enterprise-refresh>Refresh</button>
        </div>
        <div class="enterprise-list">${_renderMembers()}</div>
      </div>
      <div>
        <div class="enterprise-section-head">
          <div>
            <span class="enterprise-eyebrow">Access</span>
            <h3>Roles</h3>
          </div>
        </div>
        <div class="enterprise-list">${_renderRoles()}</div>
      </div>
    </section>

    <section class="enterprise-section">
      <div class="enterprise-section-head">
        <div>
          <span class="enterprise-eyebrow">Domain Runtime</span>
          <h3>Business Domains</h3>
        </div>
        ${_statusPill(`${_count(overview.domains?.planned)} planned`, "planned")}
      </div>
      <div class="enterprise-domain-grid">
        ${domains.map(_renderDomainCard).join("")}
      </div>
    </section>
  `;

  el.querySelector("[data-enterprise-refresh]")?.addEventListener("click", refreshEnterprisePanel);
}

export function handleEnterpriseOverview(data) {
  enterpriseState.overview = data;
  renderEnterprisePanel();
}

export function handleEnterpriseMembers(data) {
  enterpriseState.members = data.items || [];
  renderEnterprisePanel();
}

export function handleEnterpriseOrgUnits(data) {
  enterpriseState.units = data.items || [];
  renderEnterprisePanel();
}

export function handleEnterpriseRoles(data) {
  enterpriseState.roles = data.items || [];
  enterpriseState.assignments = data.assignments || [];
  renderEnterprisePanel();
}

export function handleDomains(data) {
  enterpriseState.domains = data.items || [];
  renderEnterprisePanel();
}
