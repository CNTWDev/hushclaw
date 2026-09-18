/** Track the rendered tail without adding nodes to React-owned Markdown. */
function tailRect(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    const length = node.textContent.trimEnd().length;
    if (!length) return null;
    const range = document.createRange();
    range.setStart(node, length - 1);
    range.setEnd(node, length);
    return [...range.getClientRects()].filter(rect => rect.height && rect.width).at(-1) || null;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return null;
  if (node.matches('button, [hidden], [aria-hidden="true"], .katex-mathml, script, style')) return null;
  if (node.matches('img, video, canvas, svg, .katex, .mermaid')) {
    const rect = node.getBoundingClientRect();
    return rect.height && rect.width ? rect : null;
  }
  for (let child = node.lastChild; child; child = child.previousSibling) {
    const rect = tailRect(child);
    if (rect) return rect;
  }
  return null;
}

export function followStreamTail(bubble) {
  let frame = 0;
  let stopped = false;
  const paint = () => {
    frame = 0;
    if (stopped || !bubble.isConnected) return;
    const tail = tailRect(bubble);
    if (!tail) { bubble.removeAttribute('data-stream-tail'); return; }
    const box = bubble.getBoundingClientRect();
    // Clamp wide code/table tails to the bubble instead of creating overflow.
    const x = Math.max(0, Math.min(tail.right - box.left + 2, box.width - 3));
    const height = Math.min(tail.height, 18);
    bubble.style.setProperty('--stream-tail-x', `${x}px`);
    bubble.style.setProperty('--stream-tail-y', `${tail.bottom - box.top - height}px`);
    bubble.style.setProperty('--stream-tail-height', `${height}px`);
    bubble.setAttribute('data-stream-tail', '');
  };
  const schedule = () => {
    if (!stopped && !frame) frame = requestAnimationFrame(paint);
  };
  // React commits asynchronously; native Markdown replaces its HTML directly.
  // Observe content only so writing cursor CSS variables cannot trigger a loop.
  const mutation = new MutationObserver(schedule);
  mutation.observe(bubble, { subtree: true, childList: true, characterData: true });
  const resize = new ResizeObserver(schedule);
  resize.observe(bubble);
  bubble.addEventListener('load', schedule, true);
  bubble.addEventListener('scroll', schedule, true);
  schedule();
  return () => {
    stopped = true;
    cancelAnimationFrame(frame);
    mutation.disconnect();
    resize.disconnect();
    bubble.removeEventListener('load', schedule, true);
    bubble.removeEventListener('scroll', schedule, true);
    bubble.removeAttribute('data-stream-tail');
    for (const key of ['x', 'y', 'height']) bubble.style.removeProperty(`--stream-tail-${key}`);
  };
}
