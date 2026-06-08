import React from "react";
import { createRoot, type Root } from "react-dom/client";
import { createPortal } from "react-dom";
import { Streamdown } from "streamdown";
import "streamdown/styles.css";
import "./react-islands.css";
import { preprocessMarkdownForRendering } from "../shared/markdown-preprocess.js";

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

function flattenNodeText(node: React.ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenNodeText).join("");
  if (React.isValidElement<{ children?: React.ReactNode }>(node)) {
    return flattenNodeText(node.props.children);
  }
  return "";
}

const BOX_DRAWING_RE = /[┌┐└┘├┤┬┴┼─│╭╮╰╯╞╡╪═║╔╗╚╝╠╣╦╩╬]/;

function hasBoxDrawingContent(node: React.ReactNode): boolean {
  return BOX_DRAWING_RE.test(flattenNodeText(node));
}

function isExternalHref(href?: string): boolean {
  if (!href || typeof window === "undefined") return false;
  try {
    return new URL(href, window.location.origin).origin !== window.location.origin;
  } catch {
    return false;
  }
}

function compactUrlLabel(href: string): string {
  try {
    const url = new URL(href, typeof window !== "undefined" ? window.location.origin : "https://example.com");
    const host = url.hostname.replace(/^www\./i, "");
    const path = url.pathname === "/" ? "" : url.pathname.replace(/\/+$/, "");
    const rawTail = `${path}${url.search ? "?…" : ""}${url.hash ? "#…" : ""}`;
    const maxTail = Math.max(0, 54 - host.length);
    const tail = maxTail > 0
      ? rawTail.length > maxTail
        ? `${rawTail.slice(0, Math.max(0, maxTail - 1))}…`
        : rawTail
      : "";
    return `${host}${tail}`;
  } catch {
    return href.length > 54 ? `${href.slice(0, 53)}…` : href;
  }
}

function shouldCompactHrefLabel(href: string, label: string): boolean {
  const text = label.trim();
  if (!text) return true;
  if (text === href.trim()) return true;
  try {
    return new URL(text).toString() === new URL(href).toString();
  } catch {
    return false;
  }
}

function LinkSafetyModal({
  href,
  onClose,
}: {
  href: string;
  onClose: () => void;
}) {
  const [copied, setCopied] = React.useState(false);

  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = "";
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  const copyHref = React.useCallback(async () => {
    try {
      await navigator.clipboard.writeText(href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }, [href]);

  const openHref = React.useCallback(() => {
    window.open(href, "_blank", "noreferrer");
    onClose();
  }, [href, onClose]);

  return createPortal(
    <div
      className="md-link-modal-backdrop"
      data-md-link-modal="backdrop"
      onClick={onClose}
      onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
      }}
      role="button"
      tabIndex={0}
    >
      <div
        className="md-link-modal"
        data-md-link-modal="panel"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
        role="presentation"
      >
        <button
          className="md-link-modal-close"
          onClick={onClose}
          title="Close"
          type="button"
        >
          ×
        </button>
        <div className="md-link-modal-title">Open external link?</div>
        <p className="md-link-modal-copy">You&apos;re about to visit an external website.</p>
        <div className="md-link-modal-url">{href}</div>
        <div className="md-link-modal-actions">
          <button className="md-link-modal-btn md-link-modal-btn-secondary" onClick={copyHref} type="button">
            {copied ? "Copied" : "Copy Link"}
          </button>
          <button className="md-link-modal-btn md-link-modal-btn-primary" onClick={openHref} type="button">
            Open Link
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function CompactMarkdownLink({
  href,
  children,
  className,
  ...props
}: React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  const [isOpen, setIsOpen] = React.useState(false);
  const rawLabel = flattenNodeText(children);
  const safeHref = String(href || "");
  const external = isExternalHref(safeHref);
  const compact = external && shouldCompactHrefLabel(safeHref, rawLabel);
  const displayLabel = compact ? compactUrlLabel(safeHref) : children;
  const title = compact ? safeHref : undefined;

  if (external) {
    return (
      <>
        <button
          {...props}
          className={className}
          data-streamdown="link"
          data-md-link={compact ? "compact" : "full"}
          onClick={(event) => {
            event.preventDefault();
            setIsOpen(true);
          }}
          title={title}
          type="button"
        >
          {displayLabel}
        </button>
        {isOpen ? <LinkSafetyModal href={safeHref} onClose={() => setIsOpen(false)} /> : null}
      </>
    );
  }

  return (
    <a
      {...props}
      className={className}
      data-md-link={compact ? "compact" : "full"}
      href={safeHref}
      rel={props.rel || "noreferrer"}
      target={props.target || "_blank"}
      title={title}
    >
      {displayLabel}
    </a>
  );
}

function MarkdownPre({ children, ...props }: React.HTMLAttributes<HTMLPreElement>) {
  const isDiagram = hasBoxDrawingContent(children);
  return (
    <pre
      {...props}
      data-md-diagram={isDiagram ? "true" : undefined}
    >
      {children}
    </pre>
  );
}

function MarkdownCode({
  children,
  className,
  ...props
}: React.HTMLAttributes<HTMLElement>) {
  const isDiagram = hasBoxDrawingContent(children);
  return (
    <code
      {...props}
      className={className}
      data-md-diagram={isDiagram ? "true" : undefined}
    >
      {children}
    </code>
  );
}

function MarkdownIsland({ raw = "", surface = "chat", streaming = false }: MarkdownOptions) {
  const safeSurface = normalizeSurface(surface);
  const renderRaw = preprocessMarkdownForRendering(raw);
  return (
    <div
      className={`markdown-body markdown-surface markdown-surface-${safeSurface} react-markdown-surface`}
      data-md-surface={safeSurface}
      data-streaming={streaming ? "true" : "false"}
    >
      <Streamdown
        components={{ a: CompactMarkdownLink, pre: MarkdownPre, code: MarkdownCode }}
        controls={false}
        isAnimating={false}
        mode={streaming ? "streaming" : "static"}
        normalizeHtmlIndentation
      >
        {renderRaw}
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
