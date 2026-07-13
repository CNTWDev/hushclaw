/**
 * motion.js — restrained interaction feedback for the WebUI.
 */

const INTERACTIVE_SELECTOR = [
  "button",
  "a",
  "[role=button]",
  ".sidebar-session",
  ".session-runtime-card",
  ".tool-line",
  ".workbench-activity-item",
].join(",");

let _hovered = null;
let _pressedTimer = 0;
let _pressed = null;

function _interactiveTarget(node) {
  if (!(node instanceof Element)) return null;
  return node.closest(INTERACTIVE_SELECTOR);
}

function _setHovered(next) {
  if (_hovered === next) return;
  _hovered?.classList.remove("hc-pointer-target", "hc-pointer-active");
  _hovered = next;
  _hovered?.classList.add("hc-pointer-target", "hc-pointer-active");
}

document.addEventListener("pointerover", (event) => {
  if (event.pointerType && event.pointerType !== "mouse") return;
  _setHovered(_interactiveTarget(event.target));
});

document.addEventListener("pointerout", (event) => {
  if (!_hovered) return;
  const related = event.relatedTarget;
  if (related instanceof Node && _hovered.contains(related)) return;
  _setHovered(null);
});

document.addEventListener("pointerdown", (event) => {
  if (event.pointerType && event.pointerType !== "mouse") return;
  const target = _interactiveTarget(event.target);
  if (!target) return;
  _pressed?.classList.remove("hc-pointer-pressed");
  _pressed = target;
  target.classList.add("hc-pointer-pressed");
  if (_pressedTimer) clearTimeout(_pressedTimer);
  _pressedTimer = window.setTimeout(() => {
    target.classList.remove("hc-pointer-pressed");
    if (_pressed === target) _pressed = null;
    _pressedTimer = 0;
  }, 180);
});
