/**
 * HushClaw Web UI — app.js (entry point)
 *
 * This file is intentionally thin. All functionality lives in modules/:
 *   state.js      — shared state, DOM refs, utility functions
 *   chat.js       — chat rendering, markdown, thinking indicator
 *   settings.js   — settings modal (5 tabs), config status handler, save
 *   panels.js     — sessions sidebar, agents, memories, skills panels
 *   tasks.js      — tasks panel (todos + scheduled tasks)
 *   websocket.js  — WebSocket connection and message dispatcher
 *   events.js     — sendMessage, UI helpers, all event listeners, boot
 *
 * Importing events.js triggers the full boot sequence (registers all listeners,
 * displays the connecting message, and calls connect()).
 */

import "./modules/events.js";
