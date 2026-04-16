/**
 * panels/skills.js — Skills panel: list, install, create, export, import.
 */

import {
  els, skills, learning, send, escHtml, showSkillToast,
} from "../state.js";
import { openConfirm } from "../modal.js";

// ── Skills panel handlers ─────────────────────────────────────────────────

export function handleSkillsList(data) {
  skills.installed = data.items || [];
  skills.skillDir  = data.skill_dir || "";
  skills.userSkillDir = data.user_skill_dir || "";
  skills.configured = Boolean(data.configured);
  if (els.skillDirBadge) {
    els.skillDirBadge.textContent = skills.userSkillDir
      ? `user-skills: ${skills.userSkillDir}`
      : "user-skills: default";
  }
  renderSkillsPanel();
}

export function handleSkillRepos() {}  // no-op stub — marketplace removed

export function handleSkillInstallResult(data) {
  skills.installing.delete(data.url || data.slug || "");
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
  // skills list is auto-refreshed by server pushing "skills" message after delete
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
        ${(!s.builtin && s.scope !== "system") ? `
          <button class="skill-export-single-btn" data-name="${escHtml(s.name)}" title="Export this skill as ZIP">↓</button>
          <button class="skill-delete-btn" data-name="${escHtml(s.name)}" title="Delete skill">Delete</button>
        ` : ""}
      </div>
      ${s.description ? `<div class="skill-item-desc">${escHtml(s.description)}</div>` : ""}
      ${unavailReason}
      ${installHints}
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
              <span class="learning-item-title">${escHtml(r.task_fingerprint || "general")}</span>
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
            <span class="learning-outcome-fp">${escHtml(o.task_fingerprint || "general")}</span>
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

  const _byName = (a, b) => (a.name || "").localeCompare(b.name || "", undefined, { sensitivity: "base" });
  const systemSkills  = skills.installed.filter(s => s.scope === "system").sort(_byName);
  const userSkills    = skills.installed.filter(s => !s.builtin && s.scope !== "builtin" && s.scope !== "system").sort(_byName);
  const builtinSkills = skills.installed.filter(s =>  s.builtin || s.scope === "builtin").sort(_byName);

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
    </div>`;
  c.appendChild(toolbar);

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

  // ── Installed Skills ──────────────────────────────────────────────────────
  const sec1 = document.createElement("div");
  sec1.className = "skills-section";

  const nonBuiltinCount = systemSkills.length + userSkills.length;
  let installedHtml = `
    <div class="skills-section-header">
      Installed <span class="skills-count">${skills.installed.length}</span>
    </div>`;

  if (!skills.configured && !skills.installed.length) {
    installedHtml += `
      <div class="skill-notice">
        <strong>No skills installed.</strong><br>
        Install skills from a Git URL, upload a ZIP, or create one with the <em>+ New Skill</em> button.
      </div>`;
  } else {
    if (systemSkills.length) {
      installedHtml += `
        <div class="skills-group-header">System</div>
        <div class="skills-user-list">`;
      systemSkills.forEach(s => { installedHtml += _buildSkillItem(s); });
      installedHtml += `</div>`;
    }
    if (userSkills.length) {
      installedHtml += `
        ${systemSkills.length ? `<div class="skills-group-header">User</div>` : ""}
        <div class="skills-user-list">`;
      userSkills.forEach(s => { installedHtml += _buildSkillItem(s); });
      installedHtml += `</div>`;
    }
    if (!nonBuiltinCount) {
      installedHtml += `<div class="skills-empty-user">No user skills yet — create one above or add from Git below.</div>`;
    }
    if (builtinSkills.length) {
      installedHtml += `
        <button class="skills-builtin-toggle" id="skills-builtin-toggle" type="button">
          <span class="skills-builtin-arrow">▶</span>
          Built-in Skills
          <span class="skills-count">${builtinSkills.length}</span>
        </button>
        <div class="skills-builtin-list" id="skills-builtin-list" style="display:none">`;
      builtinSkills.forEach(s => { installedHtml += _buildSkillItem(s); });
      installedHtml += `</div>`;
    }
  }

  sec1.innerHTML = installedHtml;
  c.appendChild(sec1);

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
    </div>`;
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

  // ── Wiring: Builtin accordion ─────────────────────────────────────────────
  document.getElementById("skills-builtin-toggle")?.addEventListener("click", () => {
    const list  = document.getElementById("skills-builtin-list");
    const arrow = document.querySelector("#skills-builtin-toggle .skills-builtin-arrow");
    if (!list) return;
    const open = list.style.display === "none";
    list.style.display = open ? "" : "none";
    if (arrow) arrow.textContent = open ? "▼" : "▶";
  });

  // ── Wiring: Export single skill ───────────────────────────────────────────
  sec1.querySelectorAll(".skill-export-single-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      send({ type: "export_skills", names: [btn.dataset.name] });
      showSkillToast(`Exporting "${btn.dataset.name}"…`, "ok");
    });
  });

  // ── Wiring: Delete ────────────────────────────────────────────────────────
  sec1.querySelectorAll(".skill-delete-btn").forEach((btn) => {
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
