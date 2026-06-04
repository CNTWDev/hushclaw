/**
 * panels/skills.js — Skills panel: list, install, create, export, import.
 */

import {
  els, skills, learning, send, escHtml, showSkillToast, SPINNERS,
} from "../state.js";
import { openConfirm } from "../modal.js";

let _skillSearchTimer = null;

// ── Install progress state ────────────────────────────────────────────────
const _installLogs = new Map(); // url → string[]
let _installSpinTimer = null;

function _spinChar() {
  return SPINNERS[Math.floor(Date.now() / 120) % SPINNERS.length];
}

function _urlLabel(url) {
  return url.replace(/\.git$/, "").split("/").pop() || url;
}

function _updateInstallLogEl(url, line) {
  const items = document.querySelectorAll(".skill-installing-item");
  for (const item of items) {
    if (item.dataset.url !== url) continue;
    const linesEl = item.querySelector(".skill-installing-lines");
    if (linesEl && line) {
      const el = document.createElement("div");
      el.className = "skill-installing-line";
      el.textContent = line;
      linesEl.appendChild(el);
      while (linesEl.children.length > 10) linesEl.removeChild(linesEl.firstChild);
      linesEl.scrollTop = linesEl.scrollHeight;
    }
    break;
  }
}

export function handleSkillInstallProgress(data) {
  const url = data.url || "";
  const msg = data.message || "";
  if (!url) return;
  if (!_installLogs.has(url)) _installLogs.set(url, []);
  if (msg) _installLogs.get(url).push(msg);
  _updateInstallLogEl(url, msg);
}

function _formatTaskFingerprint(value) {
  const raw = String(value || "general").trim();
  if (!raw) return "General Assistance";
  return raw
    .split("_")
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

// ── Skills panel handlers ─────────────────────────────────────────────────

export function handleSkillsList(data) {
  skills.installed = data.items || [];
  skills.catalog = data.catalog || data.items || [];
  skills.skillDir  = data.skill_dir || "";
  skills.userSkillDir = data.user_skill_dir || "";
  skills.configured = Boolean(data.configured);
  skills.total = Number(data.total || skills.installed.length || 0);
  skills.counts = data.counts || {};
  const filters = data.filters || {};
  skills.query = filters.q ?? skills.query ?? "";
  skills.scope = filters.scope || skills.scope || "all";
  skills.status = filters.status || skills.status || "all";
  skills.sort = filters.sort || skills.sort || "name";
  skills.offset = Number(data.offset || 0);
  skills.limit = Number(data.limit || skills.limit || 80);
  if (els.skillDirBadge) {
    els.skillDirBadge.textContent = skills.userSkillDir ? "User Skills Configured" : "Default Skill Library";
  }
  renderSkillsPanel();
}

export function handleSkillDetail(data) {
  if (!data.ok) {
    showSkillToast(`Failed to load skill: ${data.error || data.name || "unknown"}`, "err");
    return;
  }
  skills.detail = data.item || null;
  renderSkillsPanel();
}

export function handleSkillsHealth(data) {
  if (data.ok === false && data.error) {
    showSkillToast(`Health check failed: ${data.error}`, "err");
    return;
  }
  skills.health = data;
  const issues = Number(data.summary?.issues || 0);
  showSkillToast(issues ? `${issues} skill issue(s) found.` : "All skills look healthy.", issues ? "warn" : "ok");
  renderSkillsPanel();
}

export function handleSkillEnabled(data) {
  if (!data.ok) {
    showSkillToast(`Skill update failed: ${data.error || data.name}`, "err");
    return;
  }
  showSkillToast(`${data.enabled ? "Enabled" : "Disabled"} "${data.name}".`, "ok");
}

export function handleSkillRepos() {}  // no-op stub — marketplace removed

export function handleSkillInstallResult(data) {
  const key = data.url || data.slug || "";
  skills.installing.delete(key);
  _installLogs.delete(key);
  if (_installSpinTimer && skills.installing.size === 0) {
    clearInterval(_installSpinTimer);
    _installSpinTimer = null;
  }
  if (data.ok) {
    if (data.warning) {
      showSkillToast(`⚠ Installed — ${data.warning}`, "warn");
    } else {
      const added = data.repo_skill_count != null ? data.repo_skill_count : data.skill_count;
      const toolsMsg = data.bundled_tool_count ? `, ${data.bundled_tool_count} tools loaded` : "";
      const depsMsg = data.deps_installed === false ? " (pip deps failed — check manually)" : "";
      showSkillToast(`✓ Installed${added != null ? ` (${added} skill${added !== 1 ? "s" : ""}${toolsMsg})` : ""}${depsMsg}`, "ok");
    }
    send({ type: "list_skills" });
  } else {
    showSkillToast(`Install failed: ${data.error}`, "err");
  }
  renderSkillsPanel();
}

export function handleSkillSaved(data) {
  const status = document.getElementById("skill-save-status");
  if (data.ok) {
    if (status) { status.textContent = `Saved: ${data.name}`; setTimeout(() => { status.textContent = ""; }, 3000); }
    showSkillToast(`Skill "${data.name}" saved.`, "ok");
    const nameEl    = document.getElementById("skill-create-name");
    const descEl    = document.getElementById("skill-create-desc");
    const contentEl = document.getElementById("skill-create-content");
    if (nameEl)    nameEl.value    = "";
    if (descEl)    descEl.value    = "";
    if (contentEl) contentEl.value = "";
    send({ type: "list_skills" });
  } else {
    if (status) status.textContent = `Error: ${data.error}`;
    showSkillToast(`Failed to save skill: ${data.error}`, "err");
  }
}

export function handleSkillDeleted(data) {
  if (!data.ok) {
    showSkillToast(`Failed to delete skill: ${data.error || data.name}`, "err");
    return;
  }
  showSkillToast(`Skill "${data.name}" deleted.`, "ok");
  send({ type: "list_skills" });
}

export function handleSkillExportReady(data) {
  if (!data.ok) {
    showSkillToast(`Export failed: ${data.error || "unknown error"}`, "err");
    return;
  }
  try {
    const raw = atob(data.data);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const blob = new Blob([bytes], { type: "application/zip" });
    const url  = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = data.filename || "hushclaw-skills.zip";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showSkillToast(`Exported ${data.count} skill(s) → ${data.filename}`, "ok");
  } catch (err) {
    showSkillToast(`Export download failed: ${String(err)}`, "err");
  }
}

export function handleSkillImportResult(data) {
  if (data.installed && data.installed.length) {
    showSkillToast(`Installed: ${data.installed.join(", ")}`, "ok");
  }
  if (data.errors && data.errors.length) {
    showSkillToast(`Import errors: ${data.errors.map(e => e.error).join("; ")}`, "err");
  }
  if (!data.ok && !data.installed?.length) {
    showSkillToast(`Import failed: ${data.error || "unknown error"}`, "err");
  }
  send({ type: "list_skills" });
}

export function handleLearningState(data) {
  learning.profileSnapshot = data.profile_snapshot || {};
  learning.profileText = data.profile_text || "";
  learning.reflections = data.reflections || [];
  learning.skillOutcomes = data.skill_outcomes || [];
  renderSkillsPanel();
}

export function installSkillRepo(url) {
  if (!url || skills.installing.has(url)) return;
  skills.installing.add(url);
  _installLogs.set(url, []);
  if (!_installSpinTimer) {
    _installSpinTimer = setInterval(() => {
      document.querySelectorAll(".skill-installing-spin").forEach((el) => {
        el.textContent = _spinChar();
      });
    }, 120);
  }
  renderSkillsPanel();
  send({ type: "install_skill_repo", url });
}

// ── Skills panel helpers ──────────────────────────────────────────────────

function _buildSkillItem(s) {
  const available = s.available !== false;
  const scopeMap  = { user: "user", workspace: "ws", system: "sys", memory: "mem" };
  const scopeLabel = (s.scope && scopeMap[s.scope] && !s.builtin) ? scopeMap[s.scope] : null;
  const scopePill  = scopeLabel
    ? `<span class="skill-scope-pill skill-scope-${escHtml(s.scope)}">${scopeLabel}</span>` : "";
  const unavailBadge = available ? ""
    : `<span class="skill-badge-unavailable" title="${escHtml(s.reason || "Requirements not met")}">⚠ Unavailable</span>`;
  const unavailReason = (!available && s.reason)
    ? `<div class="skill-reason">${escHtml(s.reason)}</div>` : "";
  const installHints = (!available && s.install_hints && s.install_hints.length)
    ? s.install_hints.map(h =>
        `<div class="skill-install-hint">Run: <code class="skill-install-cmd" title="Click to copy"
          onclick="navigator.clipboard.writeText(${JSON.stringify(h.cmd)}).then(()=>{this.classList.add('copied');setTimeout(()=>this.classList.remove('copied'),1500)})"
        >${escHtml(h.cmd)}</code></div>`
      ).join("") : "";
  const versionBadge = s.version
    ? `<span class="skill-version" title="Version ${escHtml(s.version)}">v${escHtml(s.version)}</span>` : "";
  return `
    <div class="skill-item${available ? "" : " skill-unavailable"}">
      <div class="skill-item-row">
        <span class="skill-name">${escHtml(s.name)}</span>
        ${versionBadge}
        ${scopePill}
        ${unavailBadge}
        ${(s.scope === "user") ? `
          <button class="skill-export-single-btn" data-name="${escHtml(s.name)}" title="Export this skill as ZIP">↓</button>
          <button class="skill-delete-btn" data-name="${escHtml(s.name)}" title="Delete skill">Delete</button>
        ` : ""}
      </div>
      ${s.description ? `<div class="skill-item-desc">${escHtml(s.description)}</div>` : ""}
      ${unavailReason}
      ${installHints}
    </div>`;
}

function _sendSkillQuery(overrides = {}) {
  const payload = {
    type: "list_skills",
    q: skills.query || "",
    scope: skills.scope || "all",
    status: skills.status || "all",
    sort: skills.sort || "name",
    offset: skills.offset || 0,
    limit: skills.limit || 80,
    ...overrides,
  };
  send(payload);
}

export function refreshSkillsList(overrides = {}) {
  _sendSkillQuery(overrides);
}

function _scheduleSkillSearch(value) {
  skills.query = value;
  skills.offset = 0;
  clearTimeout(_skillSearchTimer);
  _skillSearchTimer = setTimeout(() => _sendSkillQuery({ q: skills.query, offset: 0 }), 180);
}

function _scopeLabel(scope) {
  return ({ builtin: "Built-in", system: "System", user: "User", workspace: "Workspace" }[scope] || "Unknown");
}

function _buildSkillCard(s) {
  const enabled = s.enabled !== false;
  const available = s.available !== false;
  const tags = Array.isArray(s.tags) ? s.tags.slice(0, 4) : [];
  const statusClass = !enabled ? "disabled" : (!available ? "warn" : "ok");
  const statusText = !enabled ? "Disabled" : (!available ? "Needs setup" : "Ready");
  const conflicts = s.has_conflict
    ? `<span class="skill-card-chip conflict" title="Multiple definitions found">Override x${Number(s.override_count || 0)}</span>` : "";
  const version = s.version ? `<span class="skill-card-chip">v${escHtml(s.version)}</span>` : "";
  const tagHtml = tags.map(t => `<span class="skill-card-tag">${escHtml(t)}</span>`).join("");
  return `
    <article class="skill-card ${!enabled ? "is-disabled" : ""}" data-name="${escHtml(s.name)}">
      <div class="skill-card-top">
        <div class="skill-card-icon">${escHtml((s.name || "?").slice(0, 1).toUpperCase())}</div>
        <div class="skill-card-title-wrap">
          <div class="skill-card-title">${escHtml(s.name)}</div>
          <div class="skill-card-sub">${escHtml(_scopeLabel(s.scope))}</div>
        </div>
        <span class="skill-status-dot ${statusClass}" title="${escHtml(statusText)}"></span>
      </div>
      <p class="skill-card-desc">${escHtml(s.description || "No description provided.")}</p>
      <div class="skill-card-meta">
        <span class="skill-card-chip ${statusClass}">${escHtml(statusText)}</span>
        ${version}
        ${conflicts}
      </div>
      ${tagHtml ? `<div class="skill-card-tags">${tagHtml}</div>` : ""}
      <div class="skill-card-actions">
        <button class="skill-detail-btn" data-name="${escHtml(s.name)}">Configure</button>
        ${s.editable ? `
          <button class="skill-toggle-btn" data-name="${escHtml(s.name)}" data-enabled="${enabled ? "0" : "1"}">
            ${enabled ? "Disable" : "Enable"}
          </button>` : ""}
        ${s.deletable ? `<button class="skill-delete-btn" data-name="${escHtml(s.name)}">Delete</button>` : ""}
        ${s.scope === "user" ? `<button class="skill-export-single-btn" data-name="${escHtml(s.name)}" title="Export this skill as ZIP">Export</button>` : ""}
      </div>
    </article>`;
}

function _buildSkillDetailModal() {
  const s = skills.detail;
  if (!s) return "";
  const reqs = [
    ...(s.requires_bins || []).map(v => `bin:${v}`),
    ...(s.requires_env || []).map(v => `env:${v}`),
  ];
  const chain = (s.overrides || []).map(v => `
    <div class="skill-chain-row ${v.active ? "active" : ""}">
      <span>${escHtml(_scopeLabel(v.tier))}</span>
      <code>${escHtml(v.path || "")}</code>
    </div>`).join("");
  return `
    <div class="skill-modal-backdrop" id="skill-detail-backdrop">
      <section class="skill-modal" role="dialog" aria-modal="true">
        <div class="skill-modal-head">
          <div>
            <div class="skill-modal-kicker">${escHtml(_scopeLabel(s.scope))} Skill</div>
            <h3>${escHtml(s.name)}</h3>
          </div>
          <button class="skill-modal-close" id="skill-detail-close" title="Close">×</button>
        </div>
        <div class="skill-modal-body">
          <p class="skill-modal-desc">${escHtml(s.description || "No description provided.")}</p>
          <div class="skill-detail-grid">
            <div><span>Status</span><strong>${s.enabled === false ? "Disabled" : (s.available === false ? "Needs setup" : "Ready")}</strong></div>
            <div><span>Version</span><strong>${escHtml(s.version || "Unversioned")}</strong></div>
            <div><span>Editable</span><strong>${s.editable ? "Yes" : "Read only"}</strong></div>
            <div><span>Path</span><code>${escHtml(s.directory || s.path || "")}</code></div>
          </div>
          ${reqs.length ? `<div class="skill-detail-block"><h4>Requirements</h4><div class="skill-card-tags">${reqs.map(r => `<span class="skill-card-tag">${escHtml(r)}</span>`).join("")}</div></div>` : ""}
          ${chain ? `<div class="skill-detail-block"><h4>Override Chain</h4>${chain}</div>` : ""}
          <div class="skill-detail-block">
            <h4>Preview</h4>
            <pre class="skill-preview">${escHtml(s.content_preview || "")}</pre>
          </div>
        </div>
      </section>
    </div>`;
}

export function renderSkillsPanel() {
  if (!els.skillsContent) return;
  const c = els.skillsContent;
  c.innerHTML = "";

  if (learning.reflections.length || learning.skillOutcomes.length) {
    const learningSec = document.createElement("div");
    learningSec.className = "skills-section learning-section";
    const reflHtml = learning.reflections.length
      ? learning.reflections.slice(0, 6).map((r) => `
          <div class="learning-item">
            <div class="learning-item-row">
              <span class="learning-item-title">Task Type: ${escHtml(_formatTaskFingerprint(r.task_fingerprint))}</span>
              <span class="learning-pill ${Number(r.success) ? "ok" : "warn"}">${Number(r.success) ? "success" : "issue"}</span>
            </div>
            ${r.lesson ? `<div class="learning-item-body">${escHtml(r.lesson)}</div>` : ""}
            ${r.strategy_hint ? `<div class="learning-item-meta">${escHtml(r.strategy_hint)}</div>` : ""}
          </div>
        `).join("")
      : `<div class="skill-notice">No reflections yet.</div>`;
    const outcomeHtml = learning.skillOutcomes.length
      ? learning.skillOutcomes.slice(0, 8).map((o) => `
          <div class="learning-outcome-row">
            <span class="learning-outcome-skill">${escHtml(o.skill_name || "skill")}</span>
            <span class="learning-pill ${Number(o.success) ? "ok" : "warn"}">${Number(o.success) ? "ok" : "fail"}</span>
            <span class="learning-outcome-fp">Task Type: ${escHtml(_formatTaskFingerprint(o.task_fingerprint))}</span>
          </div>
        `).join("")
      : `<div class="skill-notice">No skill outcomes yet.</div>`;
    learningSec.innerHTML = `
      <div class="skills-section-header">Learning Loop</div>
      <div class="learning-grid">
        <div class="learning-col">
          <div class="learning-col-title">Recent Reflections</div>
          ${reflHtml}
        </div>
        <div class="learning-col">
          <div class="learning-col-title">Skill Outcomes</div>
          ${outcomeHtml}
        </div>
      </div>`;
    c.appendChild(learningSec);
  }

  const userSkills = skills.installed.filter(s => s.scope === "user");

  // ── Toolbar ──────────────────────────────────────────────────────────────
  const toolbar = document.createElement("div");
  toolbar.className = "skills-toolbar";
  toolbar.innerHTML = `
    ${skills.configured
      ? `<button class="skills-new-btn" id="btn-new-skill">+ New Skill</button>`
      : `<span class="skills-toolbar-brand">Skills</span>`}
    <div class="skills-toolbar-actions">
      <label class="skills-action-btn" title="Import skills from a ZIP file">
        Import ZIP
        <input type="file" id="skill-import-input" accept=".zip" style="display:none">
      </label>
      <button class="skills-action-btn" id="skill-export-btn"
              title="Export user skills as a shareable ZIP"
              ${!userSkills.length ? "disabled" : ""}>Export ZIP</button>
      <button class="skills-action-btn" id="skill-health-btn" title="Check skill requirements and conflicts">Health</button>
    </div>`;
  c.appendChild(toolbar);

  const library = document.createElement("div");
  library.className = "skills-library";
  const counts = skills.counts || {};
  const showingTo = Math.min((skills.offset || 0) + skills.installed.length, skills.total || skills.installed.length);
  library.innerHTML = `
    <div class="skills-library-head">
      <div>
        <div class="skills-library-title">Skill Library</div>
        <div class="skills-library-sub">${Number(skills.total || 0)} matched · ${Number(counts.enabled || 0)} enabled · ${Number(counts.conflicts || 0)} conflicts</div>
      </div>
      <div class="skills-library-range">${skills.total ? `${Number(skills.offset || 0) + 1}-${showingTo}` : "0"} / ${Number(skills.total || 0)}</div>
    </div>
    <div class="skills-filterbar">
      <div class="skills-search-wrap">
        <input id="skills-search-input" type="search" placeholder="Search skills" autocomplete="off" value="${escHtml(skills.query || "")}">
      </div>
      <select id="skills-scope-filter">
        ${["all", "workspace", "user", "system", "builtin"].map(v => `<option value="${v}" ${skills.scope === v ? "selected" : ""}>${v === "all" ? "All scopes" : _scopeLabel(v)}</option>`).join("")}
      </select>
      <select id="skills-status-filter">
        ${[
          ["all", "All status"],
          ["enabled", "Enabled"],
          ["disabled", "Disabled"],
          ["unavailable", "Needs setup"],
          ["conflicts", "Conflicts"],
        ].map(([v, label]) => `<option value="${v}" ${skills.status === v ? "selected" : ""}>${label}</option>`).join("")}
      </select>
      <select id="skills-sort-filter">
        ${[
          ["name", "Name"],
          ["updated", "Recently updated"],
          ["scope", "Scope"],
          ["status", "Status"],
        ].map(([v, label]) => `<option value="${v}" ${skills.sort === v ? "selected" : ""}>${label}</option>`).join("")}
      </select>
    </div>
    ${skills.installed.length
      ? `<div class="skills-card-grid">${skills.installed.map(_buildSkillCard).join("")}</div>`
      : `<div class="skill-notice"><strong>No skills found.</strong><br>Try a different search or create/import a skill.</div>`}
    <div class="skills-pager">
      <button id="skills-prev-page" ${Number(skills.offset || 0) <= 0 ? "disabled" : ""}>Previous</button>
      <button id="skills-next-page" ${(Number(skills.offset || 0) + Number(skills.limit || 80)) >= Number(skills.total || 0) ? "disabled" : ""}>Next</button>
    </div>
    ${_buildSkillDetailModal()}`;
  c.appendChild(library);

  // ── Create Skill inline form ──────────────────────────────────────────────
  if (skills.configured) {
    const createWrap = document.createElement("div");
    createWrap.className = "skills-create-wrap";
    createWrap.id = "skills-create-wrap";
    createWrap.style.display = "none";
    createWrap.innerHTML = `
      <div class="skills-create-inner">
        <input type="text" id="skill-create-name" class="skills-create-field"
               placeholder="skill-name (kebab-case)" autocomplete="off">
        <input type="text" id="skill-create-desc" class="skills-create-field"
               placeholder="Short description (optional)" autocomplete="off">
        <textarea id="skill-create-content" class="skills-create-textarea" rows="7"
                  placeholder="Skill instructions…"></textarea>
        <div class="skills-create-footer">
          <button id="btn-skill-save">Save Skill</button>
          <button id="btn-skill-cancel" class="secondary">Cancel</button>
          <span id="skill-save-status" class="skills-create-status"></span>
        </div>
      </div>`;
    c.appendChild(createWrap);
  }

  // ── Add from Git Repo ───────────────────────────────────────────────────
  const sec2 = document.createElement("div");
  sec2.className = "skills-section skill-git-section";
  sec2.innerHTML = `
    <div class="skills-section-header">Add from Git Repo</div>
    <p class="skill-git-hint">
      Paste a public Git URL — any repo containing a <code>SKILL.md</code> file.
      Dependencies in <code>requirements.txt</code> and tools in <code>tools/*.py</code>
      are installed automatically.
    </p>
    <div class="skill-git-row">
      <input type="text" id="skill-custom-url" placeholder="https://github.com/user/my-skill" autocomplete="off">
      <button id="btn-install-custom" class="primary">Install</button>
    </div>
    ${skills.installing.size > 0 ? `
    <div class="skill-install-log">
      ${[...skills.installing].map(url => `
        <div class="skill-installing-item" data-url="${escHtml(url)}">
          <div class="skill-installing-header">
            <span class="skill-installing-spin">${_spinChar()}</span>
            <span class="skill-installing-name">${escHtml(_urlLabel(url))}</span>
          </div>
          <div class="skill-installing-lines">
            ${(_installLogs.get(url) || []).map(l => `<div class="skill-installing-line">${escHtml(l)}</div>`).join("")}
          </div>
        </div>`).join("")}
    </div>` : ""}`;
  c.appendChild(sec2);

  // ── Wiring: New Skill toggle ──────────────────────────────────────────────
  const newBtn       = document.getElementById("btn-new-skill");
  const createWrapEl = document.getElementById("skills-create-wrap");
  const _toggleCreate = (open) => {
    if (!createWrapEl) return;
    createWrapEl.style.display = open ? "" : "none";
    if (newBtn) newBtn.textContent = open ? "✕ Cancel" : "+ New Skill";
    if (open) document.getElementById("skill-create-name")?.focus();
  };
  newBtn?.addEventListener("click", () => _toggleCreate(createWrapEl.style.display === "none"));
  document.getElementById("btn-skill-cancel")?.addEventListener("click", () => _toggleCreate(false));

  // ── Wiring: Save Skill ────────────────────────────────────────────────────
  document.getElementById("btn-skill-save")?.addEventListener("click", () => {
    const name    = document.getElementById("skill-create-name")?.value.trim();
    const desc    = document.getElementById("skill-create-desc")?.value.trim();
    const content = document.getElementById("skill-create-content")?.value.trim();
    const status  = document.getElementById("skill-save-status");
    if (!name || !content) { if (status) status.textContent = "Name and content are required."; return; }
    if (status) status.textContent = "Saving…";
    send({ type: "save_skill", name, description: desc, content });
  });

  // ── Wiring: Export / Import ───────────────────────────────────────────────
  document.getElementById("skill-export-btn")?.addEventListener("click", () => {
    send({ type: "export_skills", names: [] });
    showSkillToast("Preparing skill export…", "ok");
  });
  document.getElementById("skill-health-btn")?.addEventListener("click", () => {
    send({ type: "check_skills_health" });
  });
  document.getElementById("skill-import-input")?.addEventListener("change", (ev) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    ev.target.value = "";
    const reader = new FileReader();
    reader.onload = (e) => {
      const bytes = new Uint8Array(e.target.result);
      let b64 = "";
      const CHUNK = 8192;
      for (let i = 0; i < bytes.length; i += CHUNK) {
        b64 += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
      }
      send({ type: "import_skill_zip", filename: file.name, data: btoa(b64) });
      showSkillToast(`Uploading ${file.name}…`, "ok");
    };
    reader.readAsArrayBuffer(file);
  });

  // ── Wiring: Export single skill ───────────────────────────────────────────
  library.querySelectorAll(".skill-export-single-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      send({ type: "export_skills", names: [btn.dataset.name] });
      showSkillToast(`Exporting "${btn.dataset.name}"…`, "ok");
    });
  });

  // ── Wiring: Delete ────────────────────────────────────────────────────────
  library.querySelectorAll(".skill-delete-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const skillName = btn.dataset.name;
      const confirmed = await openConfirm({
        title: "Delete skill",
        message: `Delete skill "${skillName}"? This will permanently remove its files.`,
        confirmText: "Delete",
        cancelText: "Cancel",
        dangerConfirm: true,
      });
      if (confirmed) send({ type: "delete_skill", name: skillName });
    });
  });

  library.querySelectorAll(".skill-card").forEach((el) => {
    el.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      const name = el.dataset.name;
      if (name) send({ type: "get_skill_detail", name });
    });
  });
  library.querySelectorAll(".skill-detail-btn").forEach((btn) => {
    btn.addEventListener("click", () => send({ type: "get_skill_detail", name: btn.dataset.name }));
  });
  library.querySelectorAll(".skill-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      send({ type: "set_skill_enabled", name: btn.dataset.name, enabled: btn.dataset.enabled === "1" });
    });
  });
  document.getElementById("skill-detail-close")?.addEventListener("click", () => {
    skills.detail = null;
    renderSkillsPanel();
  });
  document.getElementById("skill-detail-backdrop")?.addEventListener("click", (ev) => {
    if (ev.target.id === "skill-detail-backdrop") {
      skills.detail = null;
      renderSkillsPanel();
    }
  });
  document.getElementById("skills-search-input")?.addEventListener("input", (ev) => {
    _scheduleSkillSearch(ev.target.value || "");
  });
  document.getElementById("skills-scope-filter")?.addEventListener("change", (ev) => {
    skills.scope = ev.target.value;
    skills.offset = 0;
    _sendSkillQuery({ scope: skills.scope, offset: 0 });
  });
  document.getElementById("skills-status-filter")?.addEventListener("change", (ev) => {
    skills.status = ev.target.value;
    skills.offset = 0;
    _sendSkillQuery({ status: skills.status, offset: 0 });
  });
  document.getElementById("skills-sort-filter")?.addEventListener("change", (ev) => {
    skills.sort = ev.target.value;
    skills.offset = 0;
    _sendSkillQuery({ sort: skills.sort, offset: 0 });
  });
  document.getElementById("skills-prev-page")?.addEventListener("click", () => {
    skills.offset = Math.max(0, Number(skills.offset || 0) - Number(skills.limit || 80));
    _sendSkillQuery({ offset: skills.offset });
  });
  document.getElementById("skills-next-page")?.addEventListener("click", () => {
    skills.offset = Number(skills.offset || 0) + Number(skills.limit || 80);
    _sendSkillQuery({ offset: skills.offset });
  });

  // ── Wiring: Git install ───────────────────────────────────────────────────
  const customInput = sec2.querySelector("#skill-custom-url");
  const customBtn   = sec2.querySelector("#btn-install-custom");
  const _doInstall  = () => {
    const url = customInput.value.trim();
    if (url) { installSkillRepo(url); customInput.value = ""; }
  };
  customBtn.addEventListener("click", _doInstall);
  customInput.addEventListener("keydown", (e) => { if (e.key === "Enter") _doInstall(); });
}
