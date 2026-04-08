/**
 * HushClaw Web UI — app.js (entry point)
 *
 * This file is intentionally thin. All functionality lives in modules/:
 *   state.js      — shared state, DOM refs, utility functions
 *   chat.js       — chat rendering, markdown, thinking indicator
 *   settings.js   — settings modal (5 tabs), config status handler, save
 *   panels.js     — sessions sidebar, agents, memories, skills panels
 *   tasks.js      — tasks panel (todos + scheduled tasks)
 *   theme.js      — ui theme mode (auto/light/dark)
 *   websocket.js  — WebSocket connection and message dispatcher
 *   events.js     — sendMessage, UI helpers, all event listeners, boot
 *   plugin-host.js— side-panel plugin registry
 *
 * Side-panel plugins (self-contained, loaded after core):
 *   transsion/    — Community Forum (Transsion SSO + forum UI)
 *
 * Importing events.js triggers the full boot sequence (registers all listeners,
 * displays the connecting message, and calls connect()).
 */

import "./modules/events.js";
import "./transsion/index.js";
