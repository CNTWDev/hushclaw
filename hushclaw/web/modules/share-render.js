import { renderMarkdown } from "./markdown.js";

function mk(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

function normalizeTemplate(template = "auto", theme = "dark") {
  if (template === "dark") return { cardMode: "dark", cardTemplate: "dark" };
  if (template === "ink") return { cardMode: "light", cardTemplate: "ink" };
  if (template === "folio") return { cardMode: "light", cardTemplate: "folio" };
  if (template === "blueprint") return { cardMode: "light", cardTemplate: "blueprint" };
  if (template === "halo") return { cardMode: "light", cardTemplate: "halo" };
  if (theme === "light") return { cardMode: "light", cardTemplate: "ink" };
  return { cardMode: "dark", cardTemplate: "dark" };
}

function waitForRenderAssets(node) {
  const fontReady = document.fonts?.ready?.catch?.(() => {}) || Promise.resolve();
  const imgs = Array.from(node.querySelectorAll("img"));
  const imageReady = Promise.allSettled(imgs.map((img) => {
    if (img.complete && img.naturalWidth > 0) return Promise.resolve();
    if (typeof img.decode === "function") return img.decode().catch(() => {});
    return new Promise((resolve) => {
      img.addEventListener("load", resolve, { once: true });
      img.addEventListener("error", resolve, { once: true });
    });
  }));
  return Promise.all([fontReady, imageReady]);
}

function buildShareCard(payload) {
  const { cardMode, cardTemplate } = normalizeTemplate(payload.template, payload.theme);
  const card = mk("div", "cimg-card");
  card.dataset.mode = cardMode;
  card.dataset.template = cardTemplate;

  const deco = mk("div", "cimg-deco-quote", cardTemplate === "folio" ? "❞" : "❝");
  card.appendChild(deco);

  const brandBar = mk("div", "cimg-brand-bar");
  const accent = mk("div", "cimg-accent");
  const brandInner = mk("div", "cimg-brand-inner");
  const brandLeft = mk("div", "cimg-brand-left");
  const badge = mk("div", "cimg-brand-badge", "HC");
  const brandText = mk("div", "cimg-brand-text");
  brandText.appendChild(mk("div", "cimg-brand-name", "HushClaw Reading Sheet"));
  brandText.appendChild(mk("div", "cimg-brand-slogan", "A4 / 16K Editorial Export"));
  brandLeft.appendChild(badge);
  brandLeft.appendChild(brandText);

  const brandRight = mk("div", "cimg-brand-right");
  brandRight.appendChild(mk("div", "cimg-brand-datetime", payload.datetime || ""));
  brandRight.appendChild(mk("div", "cimg-brand-attr", "Assistant Response"));

  brandInner.appendChild(brandLeft);
  brandInner.appendChild(brandRight);
  brandBar.appendChild(accent);
  brandBar.appendChild(brandInner);
  if (payload.question) {
    brandBar.appendChild(mk("div", "cimg-context", payload.question.slice(0, 180)));
  }
  card.appendChild(brandBar);

  const body = mk("div", "cimg-body");
  const content = mk("div", "cimg-content markdown-body");
  content.innerHTML = renderMarkdown(payload.content || "");
  body.appendChild(content);
  card.appendChild(body);

  const footer = mk("div", "cimg-footer");
  const footerLeft = mk("div", "cimg-footer-left");
  const avatar = mk("div", "cimg-footer-avatar");
  const avatarImg = document.createElement("img");
  avatarImg.src = "/icon.svg";
  avatarImg.alt = "";
  avatarImg.decoding = "async";
  avatarImg.loading = "eager";
  avatarImg.addEventListener("error", () => {
    avatar.textContent = "HC";
    avatarImg.remove();
  }, { once: true });
  avatar.appendChild(avatarImg);
  footerLeft.appendChild(avatar);
  footerLeft.appendChild(mk("div", "cimg-footer-name", "HushClaw"));

  const footerRight = mk("div", "cimg-footer-right");
  const footerMeta = mk("div", "cimg-footer-meta");
  footerMeta.appendChild(mk("div", "cimg-footer-brand", "Built with Memory, Skills, and Continuous Learning"));
  footerMeta.appendChild(mk("span", "cimg-footer-datetime", payload.datetime || ""));
  footerRight.appendChild(footerMeta);

  footer.appendChild(footerLeft);
  footer.appendChild(footerRight);
  card.appendChild(footer);
  return card;
}

async function render(payload) {
  const root = document.getElementById("share-render-root");
  if (!root) throw new Error("share render root missing");
  root.innerHTML = "";
  const card = buildShareCard(payload || {});
  root.appendChild(card);
  await waitForRenderAssets(card);
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  card.classList.add("render-ready");
  return {
    ok: true,
    width: Math.ceil(card.getBoundingClientRect().width || 0),
    height: Math.ceil(card.getBoundingClientRect().height || 0),
  };
}

window.__HC_SHARE_RENDER__ = { render };
