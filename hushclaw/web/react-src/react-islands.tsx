import React from "react";
import { createRoot, type Root } from "react-dom/client";
import { Streamdown } from "streamdown";
import "streamdown/styles.css";
import "./react-islands.css";

type MarkdownSurface = "chat" | "file" | "share" | "forum" | string;

type MarkdownOptions = {
  raw?: string;
  surface?: MarkdownSurface;
  streaming?: boolean;
};

type MarkdownIslandApi = {
  mount: (container: Element, options: MarkdownOptions) => void;
  update: (container: Element, options: MarkdownOptions) => void;
  unmount: (container: Element) => void;
  ready: boolean;
};

declare global {
  interface Window {
    HushClawReactMarkdown?: MarkdownIslandApi;
  }
}

const roots = new WeakMap<Element, Root>();

function normalizeSurface(surface: MarkdownSurface = "chat") {
  const value = String(surface || "chat").replace(/[^\w-]/g, "");
  return value || "chat";
}

function MarkdownIsland({ raw = "", surface = "chat", streaming = false }: MarkdownOptions) {
  const safeSurface = normalizeSurface(surface);
  return (
    <div
      className={`markdown-body markdown-surface markdown-surface-${safeSurface} react-markdown-surface`}
      data-md-surface={safeSurface}
      data-streaming={streaming ? "true" : "false"}
    >
      <Streamdown
        animated
        isAnimating={streaming}
        mode={streaming ? "streaming" : "static"}
        normalizeHtmlIndentation
      >
        {raw}
      </Streamdown>
    </div>
  );
}

function render(container: Element, options: MarkdownOptions) {
  let root = roots.get(container);
  if (!root) {
    root = createRoot(container);
    roots.set(container, root);
  }
  root.render(<MarkdownIsland {...options} />);
}

window.HushClawReactMarkdown = {
  ready: true,
  mount: render,
  update: render,
  unmount(container: Element) {
    const root = roots.get(container);
    if (!root) return;
    root.unmount();
    roots.delete(container);
  },
};
