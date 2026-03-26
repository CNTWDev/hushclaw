/**
 * panels.js — Sessions sidebar, agents panel, memories panel, skills panel, tab switching.
 */

import {
  state, els, skills, agentsState,
  send, escHtml, showSkillToast, showToast,
} from "./state.js";

// ── Tab switching ──────────────────────────────────────────────────────────

export function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${tab}`);
  });
  const footer = document.querySelector("footer");
  if (footer) footer.style.display = tab === "chat" ? "" : "none";
  if (tab === "memories") send({ type: "list_memories", limit: 20 });
  if (tab === "agents") send({ type: "list_agents" });
  if (tab === "skills") {
    send({ type: "list_skills" });
    loadSkillMarketplace();
  }
  if (tab === "tasks") {
    send({ type: "list_todos" });
    send({ type: "list_scheduled_tasks" });
    // Import lazily to avoid circular dependency; tasks module handles its own populate.
    import("./tasks.js").then(({ populateSchedAgentSelect }) => populateSchedAgentSelect());
  }
}

// ── Agents panel ──────────────────────────────────────────────────────────

export function populateAgents(items) {
  state.agents = items.length ? items : [{ name: "default", description: "" }];

  els.agentSelect.innerHTML = "";
  if (!items.length) {
    const opt = document.createElement("option");
    opt.value = "default"; opt.textContent = "default";
    els.agentSelect.appendChild(opt);
    return;
  }
  items.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.name;
    opt.textContent = a.name + (a.description ? ` — ${a.description}` : "");
    if (a.name === state.agent) opt.selected = true;
    els.agentSelect.appendChild(opt);
  });
}

export function renderAgentsPanel(items) {
  if (items) agentsState.items = items;
  const el = document.getElementById("agents-list");
  if (!el) return;

  if (agentsState.addingNew) {
    el.innerHTML = `
      <div class="agent-edit-form">
        <div class="agent-edit-title">New Agent</div>
        <label>Name <input id="anew-name" type="text" placeholder="my-agent" autocomplete="off"></label>
        <label>Description <input id="anew-desc" type="text" placeholder="What does this agent do?" autocomplete="off"></label>
        <label>Model <input id="anew-model" type="text" placeholder="(leave blank to inherit)" autocomplete="off"></label>
        <label>System Prompt <textarea id="anew-system" rows="4" placeholder="You are…"></textarea></label>
        <label>Instructions <textarea id="anew-instr" rows="3" placeholder="Always reply in…"></textarea></label>
        <div class="agent-edit-actions">
          <button id="btn-anew-submit">Create</button>
          <button id="btn-anew-cancel" class="secondary">Cancel</button>
        </div>
      </div>`;
    el.querySelector("#btn-anew-cancel").addEventListener("click", () => {
      agentsState.addingNew = false;
      renderAgentsPanel();
    });
    el.querySelector("#btn-anew-submit").addEventListener("click", () => {
      const name = el.querySelector("#anew-name").value.trim();
      if (!name) { alert("Agent name is required."); return; }
      send({
        type: "create_agent",
        name,
        description: el.querySelector("#anew-desc").value.trim(),
        model: el.querySelector("#anew-model").value.trim(),
        system_prompt: el.querySelector("#anew-system").value,
        instructions: el.querySelector("#anew-instr").value,
      });
      agentsState.addingNew = false;
    });
    return;
  }

  if (!agentsState.items.length) {
    el.innerHTML = '<div class="empty-state">No agents yet.</div>';
    return;
  }
  el.innerHTML = "";
  agentsState.items.forEach((a) => {
    const isExpanded = agentsState.expandedAgent === a.name;
    const editBadge = a.editable ? '' : ' <span class="agent-badge">config</span>';
    const row = document.createElement("div");
    row.className = "list-item agent-item";
    row.dataset.name = a.name;

    let detailHtml = "";
    if (isExpanded) {
      const def = agentsState.agentDetail;
      if (!def) {
        detailHtml = '<div class="agent-detail-loading">Loading…</div>';
      } else if (agentsState.editingAgent === a.name) {
        detailHtml = `
          <div class="agent-edit-form">
            <label>Description <input id="aedit-desc" type="text" value="${escHtml(def.description || "")}" autocomplete="off"></label>
            <label>Model <input id="aedit-model" type="text" value="${escHtml(def.model || "")}" autocomplete="off"></label>
            <label>System Prompt <textarea id="aedit-system" rows="5">${escHtml(def.system_prompt || "")}</textarea></label>
            <label>Instructions <textarea id="aedit-instr" rows="3">${escHtml(def.instructions || "")}</textarea></label>
            <div class="agent-edit-actions">
              <button class="btn-aedit-save" data-name="${escHtml(a.name)}">Save</button>
              <button class="btn-aedit-cancel secondary">Cancel</button>
            </div>
          </div>`;
      } else {
        const sysPrev = def.system_prompt ? escHtml(def.system_prompt) : '<em>—</em>';
        const instrPrev = def.instructions ? escHtml(def.instructions) : '<em>—</em>';
        const modelLine = def.model ? escHtml(def.model) : '<em>inherited</em>';
        const editBtn = def.editable
          ? `<button class="btn-aedit-open secondary" data-name="${escHtml(a.name)}">Edit</button>` : "";
        const delBtn = def.editable
          ? `<button class="btn-adelete danger" data-name="${escHtml(a.name)}">Delete</button>` : "";
        detailHtml = `
          <div class="agent-detail">
            <div class="agent-detail-row"><span class="agent-detail-label">Model:</span> ${modelLine}</div>
            <div class="agent-detail-row"><span class="agent-detail-label">System Prompt:</span><pre class="agent-detail-pre">${sysPrev}</pre></div>
            <div class="agent-detail-row"><span class="agent-detail-label">Instructions:</span><pre class="agent-detail-pre">${instrPrev}</pre></div>
            <div class="agent-edit-actions">${editBtn}${delBtn}</div>
          </div>`;
      }
    }

    row.innerHTML = `
      <div class="agent-item-header">
        <span class="agent-item-name">${escHtml(a.name)}${editBadge}</span>
        <span class="agent-item-desc">${escHtml(a.description || "")}</span>
        <button class="muted-btn small btn-agent-toggle" data-name="${escHtml(a.name)}">${isExpanded ? "▲" : "▼"}</button>
      </div>
      ${detailHtml}`;

    row.querySelector(".btn-agent-toggle").addEventListener("click", () => {
      const name = a.name;
      if (agentsState.expandedAgent === name) {
        agentsState.expandedAgent = null;
        agentsState.agentDetail = null;
        agentsState.editingAgent = null;
        renderAgentsPanel();
      } else {
        agentsState.expandedAgent = name;
        agentsState.agentDetail = null;
        agentsState.editingAgent = null;
        renderAgentsPanel();
        send({ type: "get_agent", name });
      }
    });

    const editBtnEl = row.querySelector(".btn-aedit-open");
    if (editBtnEl) editBtnEl.addEventListener("click", () => {
      agentsState.editingAgent = a.name;
      renderAgentsPanel();
    });

    const saveBtnEl = row.querySelector(".btn-aedit-save");
    if (saveBtnEl) saveBtnEl.addEventListener("click", () => {
      const payload = {
        type: "update_agent",
        name: a.name,
        description: row.querySelector("#aedit-desc")?.value,
        model: row.querySelector("#aedit-model")?.value,
        system_prompt: row.querySelector("#aedit-system")?.value,
        instructions: row.querySelector("#aedit-instr")?.value,
      };
      send(payload);
      agentsState.editingAgent = null;
      agentsState.expandedAgent = null;
      agentsState.agentDetail = null;
    });

    const cancelEditBtnEl = row.querySelector(".btn-aedit-cancel");
    if (cancelEditBtnEl) cancelEditBtnEl.addEventListener("click", () => {
      agentsState.editingAgent = null;
      renderAgentsPanel();
    });

    const delBtnEl = row.querySelector(".btn-adelete");
    if (delBtnEl) delBtnEl.addEventListener("click", () => {
      if (!confirm(`Delete agent '${a.name}'?`)) return;
      send({ type: "delete_agent", name: a.name });
    });

    el.appendChild(row);
  });
}

export function handleAgentDetail(def) {
  if (!def) return;
  agentsState.agentDetail = def;
  renderAgentsPanel();
}

// ── Sessions sidebar ──────────────────────────────────────────────────────

export function loadSession(session_id) {
  state._activeSessionId = session_id;
  document.querySelectorAll(".sidebar-session").forEach((el) => {
    el.classList.toggle("active", el.dataset.sessionId === session_id);
  });
  send({ type: "get_session_history", session_id });
}

export function renderSessions(items) {
  const list = document.getElementById("sessions-list");
  if (!list) return;
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<div class="empty-state" style="padding:12px;font-size:11px">No sessions</div>';
    state._firstSessionLoad = false;
    return;
  }

  items.forEach((s) => {
    const el = document.createElement("div");
    el.className = "sidebar-session" + (s.session_id === state._activeSessionId ? " active" : "");
    el.dataset.sessionId = s.session_id;

    const shortId = (s.session_id || "—").slice(-12);
    const lastTs  = s.last_turn ? new Date(s.last_turn * 1000).toLocaleDateString() : "";

    el.innerHTML = `
      <div class="sidebar-session-info">
        <div class="sidebar-session-id" title="${escHtml(s.session_id || "")}">${escHtml(shortId)}</div>
        <div class="sidebar-session-meta">${s.turn_count || 0} turns${lastTs ? " · " + lastTs : ""}</div>
      </div>
      <button class="session-delete-btn" data-session-id="${escHtml(s.session_id || "")}" title="Delete session">✕</button>
    `;
    el.querySelector(".session-delete-btn").addEventListener("click", (ev) => {
      ev.stopPropagation();
      const sid = ev.target.dataset.sessionId;
      if (!sid || !confirm(`Delete session ${sid.slice(-12)}?`)) return;
      send({ type: "delete_session", session_id: sid });
    });
    el.addEventListener("click", () => loadSession(s.session_id));
    list.appendChild(el);
  });

  if (state._firstSessionLoad && items.length) {
    state._firstSessionLoad = false;
    loadSession(items[0].session_id);
  }
}

export function onSessionDeleted(sessionId, ok) {
  if (!ok) { alert(`Failed to delete session: ${sessionId}`); return; }
  const el = document.querySelector(`#sessions-list [data-session-id="${CSS.escape(sessionId)}"]`);
  if (el) el.remove();
  if (state._activeSessionId === sessionId) {
    state._activeSessionId = null;
    document.getElementById("chat-messages")?.innerHTML && (document.getElementById("chat-messages").innerHTML = "");
  }
}

// ── Memories panel ────────────────────────────────────────────────────────

export function renderMemories(items) {
  els.memoriesList.innerHTML = "";
  if (!items.length) {
    els.memoriesList.innerHTML = '<div class="empty-state">No memories found.</div>';
    return;
  }
  items.forEach((m) => {
    const noteId = m.id || m.note_id || "";
    const el = document.createElement("div");
    el.className = "list-item";
    el.dataset.noteId = noteId;
    const tags  = (m.tags || []).join(", ") || "—";
    const score = m.score != null ? ` &nbsp;|&nbsp; score: ${m.score.toFixed(2)}` : "";
    const bodyPreview = m.body ? escHtml(m.body.slice(0, 120)) + (m.body.length > 120 ? "…" : "") : "";
    el.innerHTML = `
      <div class="list-item-title">${escHtml(m.title || m.content || m.text || "")}</div>
      ${bodyPreview ? `<div class="list-item-meta">${bodyPreview}</div>` : ""}
      <div class="list-item-meta">ID: ${escHtml(noteId)} &nbsp;|&nbsp; tags: ${escHtml(tags)}${score}</div>
      <div class="list-item-actions">
        <button class="danger" data-note-id="${escHtml(noteId)}">Delete</button>
      </div>
    `;
    el.querySelector(".danger").addEventListener("click", (ev) => {
      ev.stopPropagation();
      const nid = ev.target.dataset.noteId;
      if (!nid || !confirm(`Delete memory ${nid}?`)) return;
      send({ type: "delete_memory", note_id: nid });
    });
    els.memoriesList.appendChild(el);
  });
}

export function onMemoryDeleted(noteId, ok) {
  if (!ok) { alert(`Failed to delete memory: ${noteId}`); return; }
  const el = els.memoriesList.querySelector(`[data-note-id="${CSS.escape(noteId)}"]`);
  if (el) el.remove();
  if (!els.memoriesList.children.length) {
    els.memoriesList.innerHTML = '<div class="empty-state">No memories found.</div>';
  }
}

// ── Skills panel ───────────────────────────────────────────────────────────

export function loadSkillMarketplace() {
  skills.reposLoading = true;
  skills.reposError = "";
  renderSkillsPanel();
  send({ type: "list_skill_repos" });
}

export function handleSkillsList(data) {
  skills.installed = data.items || [];
  skills.skillDir  = data.skill_dir || "";
  skills.userSkillDir = data.user_skill_dir || "";
  skills.configured = Boolean(data.configured);
  if (els.skillDirBadge) {
    els.skillDirBadge.textContent = skills.skillDir
      ? `skill_dir: ${skills.skillDir}`
      : "skill_dir: not configured";
  }
  renderSkillsPanel();
}

export function handleSkillRepos(data) {
  skills.reposLoading = false;
  skills.repos = data.items || [];
  skills.categories = data.categories || [];
  skills.reposError = data.error || "";
  skills.activeCategory = "All";
  renderSkillsPanel();
}

export function handleSkillInstallResult(data) {
  skills.installing.delete(data.url);
  if (data.ok) {
    if (data.warning) {
      showSkillToast(`⚠ ${data.repo} cloned — ${data.warning}`, "warn");
    } else {
      const added = data.repo_skill_count != null ? data.repo_skill_count : data.skill_count;
      const toolsMsg = data.bundled_tool_count ? `, ${data.bundled_tool_count} tools loaded` : "";
      const depsMsg = data.deps_installed === false ? " (deps install failed, check manually)" : "";
      showSkillToast(`✓ ${data.repo} installed (${added} new skills${toolsMsg})${depsMsg}`, "ok");
    }
    send({ type: "list_skills" });
    send({ type: "list_skill_repos" });
  } else {
    showSkillToast(`Error: ${data.error}`, "err");
  }
  renderSkillsPanel();
}

export function publishSkill(skillName, skillDesc, repoUrl) {
  send({ type: "publish_skill", skill_name: skillName, skill_description: skillDesc || "", repo_url: repoUrl || "" });
}

export function handlePublishSkillUrl(data) {
  if (!data.ok) {
    showSkillToast(`Publish error: ${data.error}`, "err");
    return;
  }
  window.open(data.url, "_blank", "noopener");
  showSkillToast(`Opening GitHub to publish "${data.skill_name}"…`, "ok");
}

export function installSkillRepo(url) {
  if (!url || skills.installing.has(url)) return;
  skills.installing.add(url);
  renderSkillsPanel();
  send({ type: "install_skill_repo", url });
}

export function renderSkillsPanel() {
  if (!els.skillsContent) return;
  const c = els.skillsContent;
  c.innerHTML = "";

  const sec1 = document.createElement("div");
  sec1.className = "skills-section";

  let installedHtml = `<div class="skills-section-header">Installed Skills <span class="skills-count">${skills.installed.length}</span></div>`;

  if (!skills.configured) {
    installedHtml += `
      <div class="skill-notice">
        <strong>skill_dir not configured.</strong><br>
        Add this to your <code>hushclaw.toml</code> to enable skills:
        <pre>[tools]\nskill_dir = "~/.hushclaw/skills"</pre>
      </div>`;
  } else if (!skills.installed.length) {
    installedHtml += `<div class="empty-state" style="padding:16px 0">No skills installed yet. Browse the marketplace below.</div>`;
  } else {
    installedHtml += `<div class="skills-installed-list">`;
    skills.installed.forEach((s) => {
      const available = s.available !== false;
      const unavailBadge = available ? "" :
        `<span class="skill-badge-unavailable" title="${escHtml(s.reason || "Requirements not met")}">⚠ Unavailable</span>`;
      const unavailReason = (!available && s.reason)
        ? `<div class="skill-reason">${escHtml(s.reason)}</div>` : "";
      installedHtml += `
        <div class="skill-installed-item${available ? "" : " skill-unavailable"}">
          <div class="skill-installed-meta">
            <span class="skill-name">${escHtml(s.name)}</span>
            ${unavailBadge}
            ${s.description ? `<span class="skill-desc">${escHtml(s.description)}</span>` : ""}
            ${unavailReason}
          </div>
          ${s.builtin ? "" : `<button class="secondary skill-publish-btn" data-name="${escHtml(s.name)}" data-desc="${escHtml(s.description || "")}">Publish</button>`}
        </div>`;
    });
    installedHtml += `</div>`;
  }
  sec1.innerHTML = installedHtml;
  c.appendChild(sec1);

  const sec2 = document.createElement("div");
  sec2.className = "skills-section";

  let mktHtml = `
    <div class="skills-section-header">
      Skill Marketplace
      <button class="secondary skill-mkt-refresh-btn" id="btn-skill-mkt-refresh">↻ Refresh</button>
    </div>`;

  if (skills.reposLoading) {
    mktHtml += `<div class="empty-state" style="padding:24px 0">Searching GitHub…</div>`;
  } else {
    if (skills.reposError) {
      mktHtml += `<div class="skill-notice skill-notice-warn">GitHub search unavailable (${escHtml(skills.reposError)}). Showing curated repos.</div>`;
    }

    if (skills.categories.length) {
      const cats = ["All", ...skills.categories.map(cat => cat.name)];
      mktHtml += `<div class="cat-tab-bar" id="cat-tab-bar">`;
      cats.forEach(name => {
        const active = name === skills.activeCategory ? " active" : "";
        mktHtml += `<button class="cat-tab${active}" data-cat="${escHtml(name)}">${escHtml(name)}</button>`;
      });
      mktHtml += `</div>`;
    }

    const activeCatNames = skills.activeCategory === "All" ? null
      : new Set((skills.categories.find(cat => cat.name === skills.activeCategory)?.skills || []).map(s => s.name));

    mktHtml += `<div class="skill-repo-list" id="skill-repo-list">`;
    skills.repos.forEach((repo) => {
      const installing = skills.installing.has(repo.url);
      const isIndex    = Boolean(repo.note);
      const btnText    = installing ? "…" : (repo.installed ? "Update" : "Install");
      const btnClass   = repo.installed ? "secondary" : "";
      const curatedBadge = repo.curated ? `<span class="skill-curated-badge">Curated</span>` : "";
      const starsHtml    = repo.stars ? `<div class="stars-badge">★ ${Number(repo.stars).toLocaleString()}</div>` : "";
      const authorHtml   = repo.author ? `<span class="repo-card-author">by ${escHtml(repo.author)}</span>` : "";
      const tagsHtml     = (repo.tags && repo.tags.length)
        ? `<div class="repo-card-tags">${repo.tags.map(t => `<span class="repo-tag">${escHtml(t)}</span>`).join("")}</div>`
        : "";
      const hidden = activeCatNames && !activeCatNames.has(repo.name) ? ' style="display:none"' : "";
      mktHtml += `
        <div class="skill-repo-card" data-name="${escHtml(repo.name)}"${hidden}>
          <div class="repo-card-left">
            <div class="repo-card-name">
              ${curatedBadge}
              <a href="${escHtml(repo.html_url)}" target="_blank" rel="noopener">${escHtml(repo.name)}</a>
              ${authorHtml}
            </div>
            ${repo.description ? `<div class="repo-card-desc">${escHtml(repo.description)}</div>` : ""}
            ${tagsHtml}
            ${repo.note ? `<div class="repo-card-note">ℹ ${escHtml(repo.note)}</div>` : ""}
          </div>
          <div class="repo-card-right">
            ${starsHtml}
            <div class="repo-card-actions">
              ${repo.installed ? '<span class="skill-installed-badge">✓</span>' : ""}
              ${isIndex
                ? `<a href="${escHtml(repo.html_url)}" target="_blank" rel="noopener" class="secondary repo-install-btn">Browse</a>`
                : `<button class="${btnClass} repo-install-btn" data-url="${escHtml(repo.url)}" ${installing ? "disabled" : ""}>${escHtml(btnText)}</button>`
              }
            </div>
          </div>
        </div>`;
    });
    mktHtml += `</div>`;
    if (!skills.repos.length) {
      mktHtml += `<div class="empty-state" style="padding:24px 0">No skill repos found. Use the custom URL below.</div>`;
    }
  }

  mktHtml += `
    <div class="skill-custom-install">
      <div class="skill-custom-label">Add custom repo</div>
      <div class="skill-custom-row">
        <input type="text" id="skill-custom-url"
               placeholder="https://github.com/user/my-skills"
               autocomplete="off">
        <button id="btn-install-custom">Install</button>
      </div>
    </div>`;

  sec2.innerHTML = mktHtml;
  c.appendChild(sec2);

  document.getElementById("btn-skill-mkt-refresh")
    ?.addEventListener("click", loadSkillMarketplace);

  sec2.querySelectorAll(".cat-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      skills.activeCategory = btn.dataset.cat;
      sec2.querySelectorAll(".cat-tab").forEach(b => b.classList.toggle("active", b.dataset.cat === skills.activeCategory));
      const catEntry = skills.categories.find(cat => cat.name === skills.activeCategory);
      const catNames = catEntry ? new Set(catEntry.skills.map(s => s.name)) : null;
      sec2.querySelectorAll(".skill-repo-card").forEach((card) => {
        const visible = !catNames || catNames.has(card.dataset.name);
        card.style.display = visible ? "" : "none";
      });
    });
  });

  document.getElementById("btn-install-custom")
    ?.addEventListener("click", () => {
      const url = document.getElementById("skill-custom-url")?.value.trim();
      if (url) installSkillRepo(url);
    });

  document.getElementById("skill-custom-url")
    ?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        const url = ev.target.value.trim();
        if (url) installSkillRepo(url);
      }
    });

  sec2.querySelectorAll(".repo-install-btn").forEach((btn) => {
    btn.addEventListener("click", () => installSkillRepo(btn.dataset.url));
  });

  sec1.querySelectorAll(".skill-publish-btn").forEach((btn) => {
    btn.addEventListener("click", () => publishSkill(btn.dataset.name, btn.dataset.desc, ""));
  });
}
