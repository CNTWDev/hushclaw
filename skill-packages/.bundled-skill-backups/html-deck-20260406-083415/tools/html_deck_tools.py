"""HTML Deck rendering tools — McKinsey-style presentation generator.

Converts a structured JSON deck spec into a self-contained HTML file with:
  - Inline SVG charts (bar, waterfall, timeline, 2×2 matrix)
  - Print-ready CSS (Ctrl+P → PDF, zero garbled text)
  - Browser slideshow mode (arrow-key navigation)
  - McKinsey color palette (navy / blue / gray / white)
  - CJK-safe system font stack

Supported slide types (13):
  cover         — Full-bleed title slide
  kpi_grid      — 1–4 large KPI numbers with delta indicators
  bar_chart     — Horizontal or vertical bar chart
  waterfall     — Bridge / waterfall financial chart
  two_column    — Side-by-side comparison layout
  three_pillars — Three strategic pillars / cards
  timeline      — Horizontal milestone timeline
  matrix_2x2    — 2×2 strategic positioning matrix
  table         — Data table with conditional cell styling
  quote         — Full-width insight / pull-quote
  bullet_list   — Hierarchical bullet points
  agenda        — Table of contents / agenda
  section       — Chapter divider (dark background)
"""
from __future__ import annotations

import json
import math
import re
from html import escape as _esc
from pathlib import Path
from uuid import uuid4

from hushclaw.tools.base import ToolResult, tool


# ── Color helpers ─────────────────────────────────────────────────────────────

_COLOR_MAP = {
    "navy":       "#002060",
    "blue":       "#005EB8",
    "blue_mid":   "#0072CE",
    "blue_light": "#0085CA",
    "blue_pale":  "#B3D4E8",
    "gray_dark":  "#404040",
    "gray":       "#6D6E71",
    "gray_light": "#B0B0B0",
    "gray_bg":    "#F5F5F5",
    "white":      "#FFFFFF",
    "red":        "#D0021B",
    "green":      "#1A7F3C",
    "amber":      "#E67E22",
    "teal":       "#0097A7",
    "purple":     "#6A1B9A",
}


def _c(name: str) -> str:
    return _COLOR_MAP.get(name, name)


# ── Shared CSS ────────────────────────────────────────────────────────────────

_BASE_CSS = """
:root {
  --navy:      #002060; --blue:       #005EB8; --blue-mid:   #0072CE;
  --blue-lt:   #0085CA; --blue-pale:  #B3D4E8; --gray-dk:    #404040;
  --gray:      #6D6E71; --gray-lt:    #B0B0B0; --gray-bg:    #F5F5F5;
  --gray-line: #E0E0E0; --white:      #FFFFFF; --red:        #D0021B;
  --green:     #1A7F3C; --amber:      #E67E22;
  --font: -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:16px}
body{font-family:var(--font);background:#1C1C1C;color:var(--gray-dk);-webkit-font-smoothing:antialiased}

/* ── Slide frame ── */
.slide{width:1280px;height:720px;position:relative;overflow:hidden;background:#fff;margin:24px auto;box-shadow:0 8px 32px rgba(0,0,0,.4)}
.slide-header{position:absolute;top:0;left:0;right:0;height:6px;background:var(--blue)}
.slide-footer{position:absolute;bottom:0;left:0;right:0;height:36px;padding:0 40px;display:flex;align-items:center;justify-content:space-between;border-top:1px solid var(--gray-line)}
.slide-footer .ft{font-size:10px;color:var(--gray-lt);letter-spacing:.02em}
.slide-content{position:absolute;top:6px;left:0;right:0;bottom:36px;padding:30px 52px 24px}

/* ── Typography ── */
.slide-title{font-size:25px;font-weight:700;color:var(--navy);line-height:1.2;letter-spacing:-.02em;margin-bottom:5px}
.slide-sub{font-size:13px;color:var(--gray);margin-bottom:18px;line-height:1.4}
.insight-bar{display:inline-block;background:var(--blue);color:#fff;font-size:12.5px;font-weight:600;padding:5px 14px;border-radius:3px;margin-bottom:16px;max-width:100%}
.footnote{position:absolute;bottom:44px;left:52px;right:52px;font-size:10px;color:var(--gray-lt);border-top:1px solid var(--gray-line);padding-top:5px}

/* ── KPI grid ── */
.kpi-grid{display:flex;gap:20px;align-items:stretch}
.kpi-item{flex:1;background:var(--gray-bg);border-radius:8px;padding:24px 20px;border-top:4px solid var(--blue);display:flex;flex-direction:column;justify-content:center}
.kpi-val{font-size:52px;font-weight:800;color:var(--navy);letter-spacing:-.03em;line-height:1;margin-bottom:7px}
.kpi-lbl{font-size:12px;font-weight:600;color:var(--gray);text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px}
.kpi-delta{font-size:14px;font-weight:600;display:flex;align-items:center;gap:4px}
.kpi-delta.up{color:var(--green)}.kpi-delta.down{color:var(--red)}.kpi-delta.neutral{color:var(--gray)}
.kpi-ctx{font-size:11px;color:var(--gray-lt);margin-top:5px}

/* ── Two-column ── */
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:28px}
.col-div{border-right:1px solid var(--gray-line);padding-right:28px}
.col-head{font-size:15px;font-weight:700;color:var(--navy);margin-bottom:9px}
.col-body{font-size:13px;color:var(--gray);line-height:1.65;margin-bottom:12px}
.col-tag{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--blue-lt);margin-bottom:7px}

/* ── Three pillars ── */
.pillars{display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px}
.pillar{background:#fff;border:1px solid var(--gray-line);border-radius:8px;padding:22px 18px;border-top:4px solid var(--blue)}
.pil-num{font-size:32px;font-weight:800;color:var(--blue);opacity:.25;line-height:1;margin-bottom:7px}
.pil-head{font-size:16px;font-weight:700;color:var(--navy);margin-bottom:9px}
.pil-body{font-size:12.5px;color:var(--gray);line-height:1.6}
.pil-bullets{list-style:none;margin-top:9px}
.pil-bullets li{font-size:12px;color:var(--gray);padding:3px 0 3px 14px;position:relative}
.pil-bullets li::before{content:"—";position:absolute;left:0;color:var(--blue)}

/* ── Bullet list ── */
.bullet-list{list-style:none}
.bullet-list>li{font-size:14.5px;color:var(--gray-dk);padding:7px 0 7px 22px;position:relative;border-bottom:1px solid var(--gray-line);line-height:1.5}
.bullet-list>li:last-child{border-bottom:none}
.bullet-list>li::before{content:"";position:absolute;left:0;top:16px;width:7px;height:7px;background:var(--blue);border-radius:50%}
.sub-list{list-style:none;margin-top:5px}
.sub-list li{font-size:12.5px;color:var(--gray);padding:3px 0 3px 15px;position:relative}
.sub-list li::before{content:"–";position:absolute;left:0;color:var(--gray-lt)}

/* ── Table ── */
.mck-table{width:100%;border-collapse:collapse;font-size:12.5px}
.mck-table th{background:var(--navy);color:#fff;padding:9px 13px;text-align:left;font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em}
.mck-table td{padding:8px 13px;border-bottom:1px solid var(--gray-line);color:var(--gray-dk)}
.mck-table tr:nth-child(even) td{background:var(--gray-bg)}
.cell-pos{color:var(--green)!important;font-weight:600}
.cell-neg{color:var(--red)!important;font-weight:600}
.cell-hi{background:#FFF9E6!important;font-weight:600}
.cell-bold{font-weight:700;color:var(--navy)!important}

/* ── Quote ── */
.quote-bar{width:56px;height:4px;background:var(--blue-lt);margin-bottom:18px}
.quote-text{font-size:29px;font-weight:700;line-height:1.38;letter-spacing:-.01em;max-width:900px;margin-bottom:24px}
.quote-attr{font-size:13px;font-style:italic}

/* ── Agenda ── */
.agenda-items{display:flex;flex-direction:column;gap:0}
.agenda-item{display:flex;align-items:center;gap:18px;padding:12px 18px;border-bottom:1px solid var(--gray-line);border-radius:4px}
.agenda-item.cur{background:var(--blue);color:#fff;border-bottom-color:transparent}
.ag-num{font-size:26px;font-weight:800;color:var(--blue);opacity:.3;min-width:40px}
.agenda-item.cur .ag-num{color:#fff;opacity:.5}
.ag-lbl{font-size:16px;font-weight:600;color:var(--navy)}
.agenda-item.cur .ag-lbl{color:#fff}
.ag-desc{font-size:11.5px;color:var(--gray);margin-top:1px}
.agenda-item.cur .ag-desc{color:rgba(255,255,255,.65)}

/* ── Section divider ── */
.section-slide{background:var(--navy);height:100%;display:flex;align-items:center;padding:0 80px;position:relative}
.sec-tag{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--blue-lt);margin-bottom:14px}
.sec-bar{width:56px;height:5px;background:var(--blue-lt);margin-bottom:18px}
.sec-title{font-size:40px;font-weight:800;color:#fff;line-height:1.15;letter-spacing:-.02em;max-width:700px}
.sec-body{font-size:15px;color:rgba(255,255,255,.55);margin-top:14px;line-height:1.55;max-width:620px}
.sec-num{position:absolute;right:56px;bottom:36px;font-size:80px;font-weight:800;color:rgba(255,255,255,.07);line-height:1}

/* ── Cover ── */
.cover-slide{background:var(--navy);height:100%;display:flex;flex-direction:column;justify-content:center;padding:58px 72px;position:relative}
.cover-accent{position:absolute;left:0;top:0;width:8px;height:100%;background:var(--blue-lt)}
.cover-glow{position:absolute;right:0;top:0;bottom:0;width:42%;background:linear-gradient(135deg,rgba(0,114,206,.18) 0%,transparent 60%);pointer-events:none}
.cover-tag{font-size:11.5px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--blue-lt);margin-bottom:18px}
.cover-title{font-size:50px;font-weight:800;color:#fff;line-height:1.1;letter-spacing:-.025em;max-width:860px;margin-bottom:18px}
.cover-sub{font-size:17px;color:rgba(255,255,255,.6);line-height:1.5;max-width:700px;margin-bottom:36px}
.cover-meta{display:flex;gap:28px}.cover-meta-item{font-size:12.5px;color:rgba(255,255,255,.45)}

/* ── Toolbar ── */
.toolbar{position:fixed;top:14px;right:14px;z-index:9999;display:flex;gap:8px}
.toolbar button{background:rgba(0,32,96,.88);color:#fff;border:none;border-radius:6px;padding:8px 14px;font-size:11.5px;cursor:pointer;font-family:var(--font);letter-spacing:.02em;backdrop-filter:blur(8px)}
.toolbar button:hover{background:var(--blue)}

@media print{
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  body{background:#fff}
  .slide{margin:0;box-shadow:none;page-break-after:always;break-after:page}
  .toolbar{display:none}
}
"""


# ── Footer & common helpers ───────────────────────────────────────────────────

def _footer(org: str, num: int, total: int) -> str:
    return (
        f'<div class="slide-footer">'
        f'<span class="ft">{_esc(org) if org else "CONFIDENTIAL"}</span>'
        f'<span class="ft">{num} / {total}</span>'
        f'</div>'
    )


def _head(title: str, insight: str, subtitle: str) -> str:
    if insight:
        return f'<div class="slide-title">{title}</div><div class="insight-bar">{insight}</div>'
    if subtitle:
        return f'<div class="slide-title">{title}</div><div class="slide-sub">{subtitle}</div>'
    return f'<div class="slide-title">{title}</div>'


def _fn(footnote: str) -> str:
    return f'<div class="footnote">{footnote}</div>' if footnote else ""


def _color_bar(color_name: str) -> str:
    """Resolve a chart bar color."""
    m = {
        "blue": "#005EB8", "blue_light": "#0085CA", "blue_pale": "#B3D4E8",
        "navy": "#002060", "gray": "#B0B0B0", "green": "#1A7F3C",
        "red": "#D0021B", "amber": "#E67E22", "teal": "#0097A7",
    }
    return m.get(color_name, color_name if color_name.startswith("#") else "#005EB8")


# ── SVG helpers ───────────────────────────────────────────────────────────────

_SVG_STYLE = (
    '<style>'
    '.cl{font-size:11.5px;fill:#6D6E71;font-family:-apple-system,"PingFang SC",Arial,sans-serif}'
    '.cv{font-size:12px;font-weight:700;fill:#404040;font-family:-apple-system,Arial,sans-serif}'
    '.du{font-size:11px;fill:#1A7F3C;font-weight:600;font-family:Arial,sans-serif}'
    '.dd{font-size:11px;fill:#D0021B;font-weight:600;font-family:Arial,sans-serif}'
    '.dn{font-size:11px;fill:#6D6E71;font-family:Arial,sans-serif}'
    '</style>'
)


def _delta_class(d: str) -> str:
    return {"up": "du", "down": "dd"}.get(d, "dn")


def _delta_arrow(d: str) -> str:
    return {"up": "▲ ", "down": "▼ "}.get(d, "")


def _svg_hbar(data: list, unit: str, svg_w: int = 1080, svg_h: int = 420) -> str:
    """Horizontal bar chart SVG."""
    if not data:
        return ""
    values = [float(d.get("value", 0)) for d in data]
    scale_max = max(math.ceil(max(values) * 1.18 / 5) * 5, 1)
    n = len(data)
    lw = 210        # label column width
    aw = svg_w - lw - 130  # bar area width
    rh = min(54, (svg_h - 30) // max(n, 1))
    bh = int(rh * 0.55)
    gap = (rh - bh) // 2
    parts = [f'<svg viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}" xmlns="http://www.w3.org/2000/svg">', _SVG_STYLE]
    for i, (d, v) in enumerate(zip(data, values)):
        bw  = int((v / scale_max) * aw)
        cy  = 20 + i * rh + gap
        lbl = _esc(str(d.get("label", "")))
        col = _color_bar(d.get("color", "blue"))
        dlt = d.get("delta", "")
        ddr = d.get("delta_dir", "neutral")
        # track
        parts.append(f'<rect x="{lw}" y="{cy}" width="{aw}" height="{bh}" fill="#F5F5F5" rx="2"/>')
        # bar
        parts.append(f'<rect x="{lw}" y="{cy}" width="{bw}" height="{bh}" fill="{col}" rx="2"/>')
        # label
        parts.append(f'<text x="{lw-10}" y="{cy+bh//2+4}" text-anchor="end" class="cl">{lbl}</text>')
        # value
        vs = _esc(f"{v:g}{unit}")
        parts.append(f'<text x="{lw+bw+8}" y="{cy+bh//2+4}" class="cv">{vs}</text>')
        # delta
        if dlt:
            dc = _delta_class(ddr)
            da = _delta_arrow(ddr)
            parts.append(f'<text x="{lw+aw+10}" y="{cy+bh//2+4}" class="{dc}">{da}{_esc(dlt)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_vbar(data: list, unit: str, svg_w: int = 1080, svg_h: int = 420) -> str:
    """Vertical bar chart SVG."""
    if not data:
        return ""
    values = [float(d.get("value", 0)) for d in data]
    scale_max = max(math.ceil(max(values) * 1.18 / 5) * 5, 1)
    n = len(data)
    lp = 54
    aw = svg_w - lp - 20
    ah = svg_h - 80
    cw = aw // max(n, 1)
    bw = int(cw * 0.55)
    parts = [f'<svg viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}" xmlns="http://www.w3.org/2000/svg">', _SVG_STYLE]
    # grid lines
    for gi in range(6):
        gy = int((svg_h - 60) * (1 - gi / 5))
        gv = scale_max * gi / 5
        parts.append(f'<line x1="{lp}" y1="{gy}" x2="{svg_w}" y2="{gy}" stroke="#E0E0E0" stroke-width="0.5" stroke-dasharray="4,3"/>')
        parts.append(f'<text x="{lp-5}" y="{gy+4}" text-anchor="end" class="cl">{gv:g}</text>')
    for i, (d, v) in enumerate(zip(data, values)):
        bh  = int((v / scale_max) * ah)
        x   = lp + i * cw + (cw - bw) // 2
        y   = svg_h - 60 - bh
        col = _color_bar(d.get("color", "blue"))
        lbl = _esc(str(d.get("label", "")))
        dlt = d.get("delta", "")
        ddr = d.get("delta_dir", "neutral")
        parts.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" fill="{col}" rx="2"/>')
        vs = _esc(f"{v:g}{unit}")
        parts.append(f'<text x="{x+bw//2}" y="{y-6}" text-anchor="middle" class="cv">{vs}</text>')
        if dlt:
            dc = _delta_class(ddr)
            da = _delta_arrow(ddr)
            parts.append(f'<text x="{x+bw//2}" y="{y-20}" text-anchor="middle" class="{dc}">{da}{_esc(dlt)}</text>')
        parts.append(f'<text x="{x+bw//2}" y="{svg_h-38}" text-anchor="middle" class="cl">{lbl}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_waterfall(items: list, unit: str, svg_w: int = 1080, svg_h: int = 440) -> str:
    """Waterfall / bridge chart SVG."""
    if not items:
        return ""
    running = 0.0
    processed = []
    for item in items:
        v  = float(item.get("value", 0))
        t  = item.get("type", "delta")
        lb = item.get("label", "")
        if t == "start":
            start, end, running = 0.0, v, v
        elif t == "end":
            start, end = 0.0, running
        else:
            start, end, running = running, running + v, running + v
        processed.append({"label": lb, "value": v, "type": t, "start": start, "end": end})

    all_v = [p["start"] for p in processed] + [p["end"] for p in processed]
    mn, mx = min(all_v), max(all_v)
    span = max(mx - mn, 1)
    lp, bp = 64, 64
    aw = svg_w - lp - 20
    ah = svg_h - bp - 30
    n  = len(processed)
    cw = aw // max(n, 1)
    bw = int(cw * 0.62)

    def ys(v: float) -> int:
        return int(bp + ah * (1 - (v - mn) / span))

    parts = [f'<svg viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}" xmlns="http://www.w3.org/2000/svg">', _SVG_STYLE]
    for gi in range(6):
        gv = mn + span * gi / 5
        gy = ys(gv)
        parts.append(f'<line x1="{lp}" y1="{gy}" x2="{svg_w}" y2="{gy}" stroke="#E0E0E0" stroke-width="0.5" stroke-dasharray="4,3"/>')
        parts.append(f'<text x="{lp-5}" y="{gy+4}" text-anchor="end" class="cl">{gv:,.0f}{unit}</text>')

    for i, p in enumerate(processed):
        x   = lp + i * cw + (cw - bw) // 2
        y1  = ys(max(p["start"], p["end"]))
        y2  = ys(min(p["start"], p["end"]))
        h   = max(abs(y2 - y1), 2)
        t, v = p["type"], p["value"]
        col = "#002060" if t in ("start", "end") else ("#1A7F3C" if v >= 0 else "#D0021B")
        parts.append(f'<rect x="{x}" y="{y1}" width="{bw}" height="{h}" fill="{col}" rx="2"/>')
        if i < n - 1 and t != "end":
            ny = ys(p["end"])
            nx = lp + (i + 1) * cw + (cw - bw) // 2
            parts.append(f'<line x1="{x+bw}" y1="{ny}" x2="{nx}" y2="{ny}" stroke="#CCCCCC" stroke-width="1" stroke-dasharray="3,2"/>')
        lv = (f"+{v:,.0f}" if (v > 0 and t == "delta") else f"{v:,.0f}")
        parts.append(f'<text x="{x+bw//2}" y="{y1-7}" text-anchor="middle" class="cv">{_esc(lv)}{unit}</text>')
        parts.append(f'<text x="{x+bw//2}" y="{svg_h-12}" text-anchor="middle" class="cl">{_esc(p["label"])}</text>')

    parts.append("</svg>")
    return "".join(parts)


def _svg_timeline(nodes: list, svg_w: int = 1140, svg_h: int = 340) -> str:
    """Horizontal milestone timeline SVG."""
    if not nodes:
        return ""
    n = len(nodes)
    ty, nr = 140, 14
    sp = (svg_w - 80) / max(n - 1, 1)
    sx = 40
    parts = [f'<svg viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}" xmlns="http://www.w3.org/2000/svg">', _SVG_STYLE]
    # track
    parts.append(f'<line x1="{sx}" y1="{ty}" x2="{sx+(n-1)*sp:.0f}" y2="{ty}" stroke="#B3D4E8" stroke-width="3"/>')

    for i, nd in enumerate(nodes):
        cx    = sx + i * sp
        done  = nd.get("done", False)
        active = nd.get("active", False)
        date  = _esc(nd.get("date", ""))
        lbl   = _esc(nd.get("label", ""))
        body  = nd.get("body", "")
        fill  = "#002060" if active else ("#005EB8" if done else "#FFFFFF")
        stroke = "#005EB8"
        parts.append(f'<circle cx="{cx:.0f}" cy="{ty}" r="{nr}" fill="{fill}" stroke="{stroke}" stroke-width="3"/>')
        if done and not active:
            parts.append(f'<text x="{cx:.0f}" y="{ty+5}" text-anchor="middle" style="font-size:12px;fill:#fff;font-weight:700;">✓</text>')
        elif active:
            parts.append(f'<circle cx="{cx:.0f}" cy="{ty}" r="5" fill="#fff"/>')
        if date:
            parts.append(f'<text x="{cx:.0f}" y="{ty-26}" text-anchor="middle" style="font-size:11px;font-weight:700;fill:#005EB8;font-family:Arial,sans-serif;">{date}</text>')
        parts.append(f'<text x="{cx:.0f}" y="{ty+34}" text-anchor="middle" style="font-size:13px;font-weight:700;fill:#002060;font-family:-apple-system,\'PingFang SC\',Arial,sans-serif;">{lbl}</text>')
        if body:
            words = str(body).split()
            lines, cur = [], ""
            for w in words:
                if len(cur) + len(w) + 1 > 16 and cur:
                    lines.append(cur); cur = w
                else:
                    cur = (cur + " " + w).strip()
            if cur:
                lines.append(cur)
            for j, ln in enumerate(lines[:3]):
                parts.append(f'<text x="{cx:.0f}" y="{ty+52+j*16}" text-anchor="middle" class="cl">{_esc(ln)}</text>')

    parts.append("</svg>")
    return "".join(parts)


# ── Slide renderers ───────────────────────────────────────────────────────────

def _r_cover(s: dict, org: str, num: int, total: int) -> str:
    title  = _esc(s.get("title", ""))
    sub    = _esc(s.get("subtitle", ""))
    tag    = _esc(s.get("tag", ""))
    date   = _esc(s.get("date", ""))
    author = _esc(s.get("author", ""))
    meta = "".join(
        f'<span class="cover-meta-item">{v}</span>'
        for v in [date, author, org] if v
    )
    return (
        f'<div class="slide">'
        f'<div class="cover-slide">'
        f'<div class="cover-accent"></div>'
        f'<div class="cover-glow"></div>'
        + (f'<div class="cover-tag">{tag}</div>' if tag else "")
        + f'<div class="cover-title">{title}</div>'
        + (f'<div class="cover-sub">{sub}</div>' if sub else "")
        + (f'<div class="cover-meta">{meta}</div>' if meta else "")
        + f'</div>'
        + _footer(org, num, total)
        + f'</div>'
    )


def _r_kpi_grid(s: dict, org: str, num: int, total: int) -> str:
    title   = _esc(s.get("title", ""))
    insight = _esc(s.get("insight", ""))
    sub     = _esc(s.get("subtitle", ""))
    fn      = _esc(s.get("footnote", ""))
    kpis    = s.get("kpis", [])
    hd      = _head(title, insight, sub)

    items_html = ""
    for k in kpis[:4]:
        val   = _esc(str(k.get("value", "")))
        lbl   = _esc(k.get("label", ""))
        dlt   = _esc(k.get("delta", ""))
        trend = k.get("trend", "neutral")
        ctx   = _esc(k.get("context", ""))
        arrow = "↑" if trend == "up" else ("↓" if trend == "down" else "→")
        delt_html = f'<div class="kpi-delta {trend}">{arrow} {dlt}</div>' if dlt else ""
        items_html += (
            f'<div class="kpi-item">'
            f'<div class="kpi-val">{val}</div>'
            f'<div class="kpi-lbl">{lbl}</div>'
            + delt_html
            + (f'<div class="kpi-ctx">{ctx}</div>' if ctx else "")
            + f'</div>'
        )
    h_used = 88 if (insight or sub) else 55
    return (
        f'<div class="slide"><div class="slide-header"></div>'
        f'<div class="slide-content">{hd}'
        f'<div class="kpi-grid" style="height:calc(100% - {h_used}px);">{items_html}</div>'
        + _fn(fn)
        + f'</div>'
        + _footer(org, num, total)
        + f'</div>'
    )


def _r_bar_chart(s: dict, org: str, num: int, total: int) -> str:
    title   = _esc(s.get("title", ""))
    insight = _esc(s.get("insight", ""))
    sub     = _esc(s.get("subtitle", ""))
    fn      = _esc(s.get("footnote", ""))
    chart   = s.get("chart", {})
    data    = chart.get("data", [])
    unit    = chart.get("unit", "")
    orient  = chart.get("orientation", "horizontal")
    svg     = _svg_hbar(data, unit) if orient == "horizontal" else _svg_vbar(data, unit)
    hd      = _head(title, insight, sub)
    h_used  = 88 if (insight or sub) else 55
    return (
        f'<div class="slide"><div class="slide-header"></div>'
        f'<div class="slide-content">{hd}'
        f'<div style="height:calc(100% - {h_used}px);overflow:hidden;">{svg}</div>'
        + _fn(fn) + f'</div>'
        + _footer(org, num, total) + f'</div>'
    )


def _r_waterfall(s: dict, org: str, num: int, total: int) -> str:
    title   = _esc(s.get("title", ""))
    insight = _esc(s.get("insight", ""))
    sub     = _esc(s.get("subtitle", ""))
    fn      = _esc(s.get("footnote", ""))
    items   = s.get("items", [])
    unit    = s.get("unit", "")
    svg     = _svg_waterfall(items, unit)
    hd      = _head(title, insight, sub)
    h_used  = 88 if (insight or sub) else 55
    return (
        f'<div class="slide"><div class="slide-header"></div>'
        f'<div class="slide-content">{hd}'
        f'<div style="height:calc(100% - {h_used}px);overflow:hidden;">{svg}</div>'
        + _fn(fn) + f'</div>'
        + _footer(org, num, total) + f'</div>'
    )


def _r_two_column(s: dict, org: str, num: int, total: int) -> str:
    title   = _esc(s.get("title", ""))
    insight = _esc(s.get("insight", ""))
    sub     = _esc(s.get("subtitle", ""))
    fn      = _esc(s.get("footnote", ""))
    left    = s.get("left", {})
    right   = s.get("right", {})

    def col(c: dict, divider: bool = False) -> str:
        cls = "col-div" if divider else ""
        out = [f'<div class="{cls}" style="height:100%;overflow-y:auto;">']
        if c.get("label"):
            out.append(f'<div class="col-tag">{_esc(c["label"])}</div>')
        if c.get("heading"):
            out.append(f'<div class="col-head">{_esc(c["heading"])}</div>')
        if c.get("body"):
            out.append(f'<div class="col-body">{_esc(c["body"])}</div>')
        if c.get("bullets"):
            lis = "".join(f'<li>{_esc(b)}</li>' for b in c["bullets"])
            out.append(f'<ul class="bullet-list">{lis}</ul>')
        if c.get("kpi"):
            k = c["kpi"] if isinstance(c["kpi"], dict) else {"value": c["kpi"]}
            out.append(
                f'<div style="margin-top:14px;">'
                f'<div style="font-size:42px;font-weight:800;color:#002060;letter-spacing:-.03em;">{_esc(str(k.get("value","")))}</div>'
                + (f'<div style="font-size:11.5px;font-weight:600;color:#6D6E71;text-transform:uppercase;letter-spacing:.04em;">{_esc(k.get("label",""))}</div>' if k.get("label") else "")
                + f'</div>'
            )
        out.append("</div>")
        return "".join(out)

    hd     = _head(title, insight, sub)
    h_used = 88 if (insight or sub) else 55
    return (
        f'<div class="slide"><div class="slide-header"></div>'
        f'<div class="slide-content">{hd}'
        f'<div class="two-col" style="height:calc(100% - {h_used}px);">'
        + col(left, divider=True) + col(right)
        + f'</div>' + _fn(fn) + f'</div>'
        + _footer(org, num, total) + f'</div>'
    )


def _r_three_pillars(s: dict, org: str, num: int, total: int) -> str:
    title   = _esc(s.get("title", ""))
    insight = _esc(s.get("insight", ""))
    sub     = _esc(s.get("subtitle", ""))
    fn      = _esc(s.get("footnote", ""))
    pillars = s.get("pillars", [])
    hd      = _head(title, insight, sub)
    h_used  = 88 if (insight or sub) else 55

    items_html = ""
    for i, p in enumerate(pillars[:3]):
        lis = "".join(f'<li>{_esc(b)}</li>' for b in p.get("bullets", []))
        bul = f'<ul class="pil-bullets">{lis}</ul>' if lis else ""
        kv  = p.get("kpi", "")
        if kv:
            k = kv if isinstance(kv, dict) else {"value": kv}
            kpi_html = (
                f'<div style="margin-top:11px;padding-top:11px;border-top:1px solid #E0E0E0;">'
                f'<span style="font-size:26px;font-weight:800;color:#005EB8;letter-spacing:-.02em;">{_esc(str(k.get("value","")))}</span>'
                + (f'<span style="font-size:11px;color:#6D6E71;margin-left:6px;">{_esc(k.get("label",""))}</span>' if k.get("label") else "")
                + f'</div>'
            )
        else:
            kpi_html = ""
        items_html += (
            f'<div class="pillar">'
            f'<div class="pil-num">0{i+1}</div>'
            f'<div class="pil-head">{_esc(p.get("heading",""))}</div>'
            + (f'<div class="pil-body">{_esc(p.get("body",""))}</div>' if p.get("body") else "")
            + bul + kpi_html + f'</div>'
        )
    return (
        f'<div class="slide"><div class="slide-header"></div>'
        f'<div class="slide-content">{hd}'
        f'<div class="pillars" style="height:calc(100% - {h_used}px);">{items_html}</div>'
        + _fn(fn) + f'</div>'
        + _footer(org, num, total) + f'</div>'
    )


def _r_timeline(s: dict, org: str, num: int, total: int) -> str:
    title   = _esc(s.get("title", ""))
    insight = _esc(s.get("insight", ""))
    sub     = _esc(s.get("subtitle", ""))
    fn      = _esc(s.get("footnote", ""))
    nodes   = s.get("nodes", [])
    svg     = _svg_timeline(nodes)
    hd      = _head(title, insight, sub)
    h_used  = 88 if (insight or sub) else 55
    return (
        f'<div class="slide"><div class="slide-header"></div>'
        f'<div class="slide-content">{hd}'
        f'<div style="height:calc(100% - {h_used}px);display:flex;align-items:center;">{svg}</div>'
        + _fn(fn) + f'</div>'
        + _footer(org, num, total) + f'</div>'
    )


def _r_matrix_2x2(s: dict, org: str, num: int, total: int) -> str:
    title   = _esc(s.get("title", ""))
    insight = _esc(s.get("insight", ""))
    sub     = _esc(s.get("subtitle", ""))
    fn      = _esc(s.get("footnote", ""))
    xl      = _esc(s.get("x_label", ""))
    yl      = _esc(s.get("y_label", ""))
    quads   = s.get("quadrants", {})

    def quad(key: str) -> str:
        q  = quads.get(key, {})
        h  = q.get("highlight", False)
        bg = "#EEF4FF" if h else "#F9F9F9"
        bd = "2px solid #0085CA" if h else "1px solid #E0E0E0"
        lc = "#002060" if h else "#404040"
        return (
            f'<div style="background:{bg};border:{bd};border-radius:6px;padding:18px 20px;display:flex;flex-direction:column;justify-content:center;">'
            f'<div style="font-size:14.5px;font-weight:700;color:{lc};margin-bottom:7px;">{_esc(q.get("label",""))}</div>'
            + (f'<div style="font-size:12px;color:#6D6E71;line-height:1.6;">{_esc(q.get("body",""))}</div>' if q.get("body") else "")
            + f'</div>'
        )

    hd     = _head(title, insight, sub)
    h_used = 88 if (insight or sub) else 55
    return (
        f'<div class="slide"><div class="slide-header"></div>'
        f'<div class="slide-content">{hd}'
        f'<div style="display:flex;height:calc(100% - {h_used}px);gap:10px;">'
        f'<div style="display:flex;align-items:center;width:18px;">'
        f'<div style="writing-mode:vertical-lr;transform:rotate(180deg);font-size:10.5px;font-weight:600;color:#6D6E71;letter-spacing:.06em;text-transform:uppercase;white-space:nowrap;">{yl}</div>'
        f'</div>'
        f'<div style="flex:1;display:flex;flex-direction:column;gap:8px;">'
        f'<div style="flex:1;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:8px;">'
        + quad("top_left") + quad("top_right") + quad("bottom_left") + quad("bottom_right")
        + f'</div>'
        f'<div style="text-align:center;font-size:10.5px;font-weight:600;color:#6D6E71;letter-spacing:.06em;text-transform:uppercase;">{xl}</div>'
        f'</div></div>'
        + _fn(fn) + f'</div>'
        + _footer(org, num, total) + f'</div>'
    )


def _r_table(s: dict, org: str, num: int, total: int) -> str:
    title   = _esc(s.get("title", ""))
    insight = _esc(s.get("insight", ""))
    sub     = _esc(s.get("subtitle", ""))
    fn      = _esc(s.get("footnote", ""))
    headers = s.get("headers", [])
    rows    = s.get("rows", [])
    hd      = _head(title, insight, sub)

    ths = "".join(f'<th>{_esc(h)}</th>' for h in headers)
    _CELL_CLS = {"positive": "cell-pos", "negative": "cell-neg", "highlight": "cell-hi", "bold": "cell-bold"}
    tbody = ""
    for row in rows:
        cells = ""
        for cell in row:
            if isinstance(cell, dict):
                cls = _CELL_CLS.get(cell.get("style", ""), "")
                cells += f'<td class="{cls}">{_esc(str(cell.get("value", "")))}</td>'
            else:
                cells += f'<td>{_esc(str(cell))}</td>'
        tbody += f'<tr>{cells}</tr>'

    h_used = 88 if (insight or sub) else 55
    return (
        f'<div class="slide"><div class="slide-header"></div>'
        f'<div class="slide-content">{hd}'
        f'<div style="height:calc(100% - {h_used}px);overflow-y:auto;">'
        f'<table class="mck-table"><thead><tr>{ths}</tr></thead><tbody>{tbody}</tbody></table>'
        f'</div>'
        + _fn(fn) + f'</div>'
        + _footer(org, num, total) + f'</div>'
    )


def _r_quote(s: dict, org: str, num: int, total: int) -> str:
    text   = _esc(s.get("text", ""))
    author = _esc(s.get("author", ""))
    source = _esc(s.get("source", ""))
    tag    = _esc(s.get("tag", ""))
    dark   = s.get("bg", "white") in ("navy", "dark")
    bg_col = "#002060" if dark else "#FFFFFF"
    tc     = "#fff" if dark else "#002060"
    sc     = "rgba(255,255,255,.6)" if dark else "#6D6E71"
    return (
        f'<div class="slide" style="background:{bg_col};">'
        + (f'<div class="slide-header"></div>' if not dark else "")
        + f'<div class="slide-content" style="display:flex;flex-direction:column;justify-content:center;">'
        + (f'<div style="font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#0085CA;margin-bottom:16px;">{tag}</div>' if tag else "")
        + f'<div class="quote-bar" style="background:#0085CA;margin:0 0 18px 0;"></div>'
        + f'<div class="quote-text" style="color:{tc};">{text}</div>'
        + (f'<div class="quote-attr" style="color:{sc};"><strong>{author}</strong>' + (" · " + source if source else "") + "</div>" if author else "")
        + f'</div>'
        + _footer("", num, total)
        + f'</div>'
    )


def _r_bullet_list(s: dict, org: str, num: int, total: int) -> str:
    title   = _esc(s.get("title", ""))
    insight = _esc(s.get("insight", ""))
    sub     = _esc(s.get("subtitle", ""))
    fn      = _esc(s.get("footnote", ""))
    items   = s.get("items", [])
    two_col = s.get("two_columns", False)
    hd      = _head(title, insight, sub)
    h_used  = 88 if (insight or sub) else 55

    def item_html(it: dict | str) -> str:
        if isinstance(it, str):
            return f'<li>{_esc(it)}</li>'
        sub_lis = "".join(f'<li>{_esc(x)}</li>' for x in it.get("sub", []))
        return f'<li>{_esc(it.get("text",""))}' + (f'<ul class="sub-list">{sub_lis}</ul>' if sub_lis else "") + '</li>'

    if two_col:
        half = len(items) // 2 + len(items) % 2
        left_html  = "".join(item_html(it) for it in items[:half])
        right_html = "".join(item_html(it) for it in items[half:])
        list_html = (
            f'<div class="two-col" style="height:calc(100% - {h_used}px);">'
            f'<ul class="bullet-list">{left_html}</ul>'
            f'<ul class="bullet-list">{right_html}</ul>'
            f'</div>'
        )
    else:
        lis = "".join(item_html(it) for it in items)
        list_html = f'<ul class="bullet-list" style="height:calc(100% - {h_used}px);overflow-y:auto;">{lis}</ul>'

    return (
        f'<div class="slide"><div class="slide-header"></div>'
        f'<div class="slide-content">{hd}{list_html}'
        + _fn(fn) + f'</div>'
        + _footer(org, num, total) + f'</div>'
    )


def _r_agenda(s: dict, org: str, num: int, total: int) -> str:
    title   = _esc(s.get("title", "Today's Agenda"))
    items   = s.get("items", [])
    current = s.get("current", -1)
    items_html = ""
    for i, it in enumerate(items):
        cls  = "agenda-item cur" if i == current else "agenda-item"
        lbl  = _esc(it.get("label", ""))
        desc = _esc(it.get("desc", ""))
        items_html += (
            f'<div class="{cls}">'
            f'<div class="ag-num">0{i+1}</div>'
            f'<div><div class="ag-lbl">{lbl}</div>'
            + (f'<div class="ag-desc">{desc}</div>' if desc else "")
            + f'</div></div>'
        )
    return (
        f'<div class="slide"><div class="slide-header"></div>'
        f'<div class="slide-content">'
        f'<div class="slide-title">{title}</div>'
        f'<div class="agenda-items" style="height:calc(100% - 52px);">{items_html}</div>'
        f'</div>'
        + _footer(org, num, total) + f'</div>'
    )


def _r_section(s: dict, org: str, num: int, total: int) -> str:
    tag   = _esc(s.get("tag", ""))
    title = _esc(s.get("title", ""))
    body  = _esc(s.get("body", ""))
    snum  = _esc(str(s.get("num", "")))
    return (
        f'<div class="slide">'
        f'<div class="section-slide">'
        f'<div>'
        + (f'<div class="sec-tag">{tag}</div>' if tag else "")
        + f'<div class="sec-bar"></div>'
        f'<div class="sec-title">{title}</div>'
        + (f'<div class="sec-body">{body}</div>' if body else "")
        + f'</div>'
        + (f'<div class="sec-num">{snum}</div>' if snum else "")
        + f'</div>'
        + _footer(org, num, total) + f'</div>'
    )


# ── Dispatch ──────────────────────────────────────────────────────────────────

_RENDERERS = {
    "cover":         _r_cover,
    "kpi_grid":      _r_kpi_grid,
    "bar_chart":     _r_bar_chart,
    "waterfall":     _r_waterfall,
    "two_column":    _r_two_column,
    "three_pillars": _r_three_pillars,
    "timeline":      _r_timeline,
    "matrix_2x2":    _r_matrix_2x2,
    "table":         _r_table,
    "quote":         _r_quote,
    "bullet_list":   _r_bullet_list,
    "agenda":        _r_agenda,
    "section":       _r_section,
}


def _render_slide(slide: dict, org: str, num: int, total: int) -> str:
    t = slide.get("type", "bullet_list")
    fn = _RENDERERS.get(t)
    if not fn:
        return (
            f'<div class="slide"><div style="padding:60px;color:#D0021B;font-size:14px;">'
            f'Unknown slide type: {_esc(t)}</div></div>'
        )
    try:
        return fn(slide, org, num, total)
    except Exception as exc:
        return (
            f'<div class="slide"><div style="padding:60px;color:#D0021B;font-size:13px;">'
            f'Render error slide {num} ({_esc(t)}): {_esc(str(exc))}</div></div>'
        )


# ── HTML assembler ────────────────────────────────────────────────────────────

_PRESENT_JS = """
function togglePresent(){
  const el=document.documentElement;
  if(el.classList.contains('p')){
    el.classList.remove('p');
    document.querySelectorAll('.slide').forEach(s=>{s.style.cssText='';});
    document.body.style.cssText='';
    document.onkeydown=null;
  } else {
    el.classList.add('p');
    document.body.style.cssText='background:#000;margin:0;padding:0;overflow:hidden;';
    const sl=document.querySelectorAll('.slide');
    let c=0;
    function show(i){
      sl.forEach((s,j)=>{
        s.style.cssText=j===i
          ?'margin:0;box-shadow:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:10;'
          :'display:none;';
      });
    }
    show(c);
    document.onkeydown=function(e){
      if(e.key==='ArrowRight'||e.key===' '){c=Math.min(c+1,sl.length-1);show(c);}
      if(e.key==='ArrowLeft'){c=Math.max(c-1,0);show(c);}
      if(e.key==='Escape'){togglePresent();}
    };
  }
}
"""


def _build_html(spec: dict) -> str:
    title  = spec.get("title", "Presentation")
    org    = spec.get("org", "")
    slides = spec.get("slides", [])
    total  = len(slides)
    slides_html = "\n".join(
        _render_slide(sl, org, i + 1, total)
        for i, sl in enumerate(slides)
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>{_BASE_CSS}</style>
</head>
<body>
<div class="toolbar">
  <button onclick="window.print()">🖨&nbsp;Print / Save PDF</button>
  <button onclick="togglePresent()">▶&nbsp;Present</button>
</div>
{slides_html}
<script>{_PRESENT_JS}</script>
</body>
</html>"""


# ── Public tools ──────────────────────────────────────────────────────────────

_SLIDE_SCHEMAS: dict = {
    "cover": {
        "best_for": "Opening title slide",
        "fields": {
            "title": "string — main headline",
            "subtitle": "string — supporting sentence",
            "tag": "string (optional) — e.g. 'CONFIDENTIAL' or 'Q2 2026'",
            "date": "string (optional)",
            "author": "string (optional)",
        },
    },
    "kpi_grid": {
        "best_for": "Key metrics dashboard, financial summary (1–4 numbers)",
        "fields": {
            "title": "string",
            "insight": "string — the so-what headline (shown as blue pill)",
            "kpis": "array of {value:string, label:string, delta:string, trend:'up'|'down'|'neutral', context:string}",
            "footnote": "string (optional) — data source",
        },
    },
    "bar_chart": {
        "best_for": "Comparison, ranking, performance by segment",
        "fields": {
            "title": "string",
            "insight": "string",
            "chart": {
                "orientation": "'horizontal' | 'vertical'",
                "unit": "string e.g. '%' or '$M' or ''",
                "data": "array of {label:string, value:number, color:'blue'|'blue_light'|'gray'|'green'|'red'|'amber', delta:string, delta_dir:'up'|'down'|'neutral'}",
            },
            "footnote": "string (optional)",
        },
    },
    "waterfall": {
        "best_for": "Financial bridge, revenue/cost decomposition",
        "fields": {
            "title": "string",
            "insight": "string",
            "unit": "string e.g. '$M' or '%'",
            "items": "array of {label:string, value:number, type:'start'|'delta'|'end'}",
            "footnote": "string (optional)",
        },
    },
    "two_column": {
        "best_for": "Before/after, problem vs solution, Option A vs B, current vs target",
        "fields": {
            "title": "string",
            "insight": "string",
            "left":  {"label": "string (column header)", "heading": "string", "body": "string", "bullets": "string[]", "kpi": "{value,label}"},
            "right": {"label": "string (column header)", "heading": "string", "body": "string", "bullets": "string[]", "kpi": "{value,label}"},
            "footnote": "string",
        },
    },
    "three_pillars": {
        "best_for": "Three strategic pillars, initiatives, or findings",
        "fields": {
            "title": "string",
            "insight": "string",
            "pillars": "array (max 3) of {heading:string, body:string, bullets:string[], kpi:{value,label}}",
            "footnote": "string",
        },
    },
    "timeline": {
        "best_for": "Roadmap, milestones, historical events",
        "fields": {
            "title": "string",
            "insight": "string",
            "nodes": "array of {label:string, date:string, body:string, done:bool, active:bool}",
            "footnote": "string",
        },
    },
    "matrix_2x2": {
        "best_for": "BCG matrix, priority grid, risk/impact, strategic positioning",
        "fields": {
            "title": "string",
            "insight": "string",
            "x_label": "string (horizontal axis, low→high left→right)",
            "y_label": "string (vertical axis, low→high bottom→top)",
            "quadrants": {
                "top_left":     {"label": "string", "body": "string", "highlight": "bool"},
                "top_right":    {"label": "string", "body": "string", "highlight": "bool"},
                "bottom_left":  {"label": "string", "body": "string", "highlight": "bool"},
                "bottom_right": {"label": "string", "body": "string", "highlight": "bool"},
            },
        },
    },
    "table": {
        "best_for": "Scorecard, comparison table, data summary with conditional formatting",
        "fields": {
            "title": "string",
            "insight": "string",
            "headers": "string[]",
            "rows": "array of arrays; each cell is a string or {value:string, style:'positive'|'negative'|'highlight'|'bold'}",
            "footnote": "string",
        },
    },
    "quote": {
        "best_for": "Key insight, exec-level statement, client quote, chapter thesis",
        "fields": {
            "text": "string — the insight sentence (max ~200 chars)",
            "author": "string (optional)",
            "source": "string (optional)",
            "tag": "string (optional) e.g. 'Key Insight'",
            "bg": "'white' | 'navy' (navy = dark full-bleed)",
        },
    },
    "bullet_list": {
        "best_for": "Structured findings, recommendations, requirements, action plan",
        "fields": {
            "title": "string",
            "insight": "string",
            "items": "array of strings or {text:string, sub:string[]}",
            "two_columns": "bool (split items into two columns)",
            "footnote": "string",
        },
    },
    "agenda": {
        "best_for": "Table of contents, meeting agenda, section preview",
        "fields": {
            "title": "string",
            "items": "array of {label:string, desc:string}",
            "current": "int — index of active item (-1 for full TOC)",
        },
    },
    "section": {
        "best_for": "Chapter/section divider (dark navy background)",
        "fields": {
            "tag":   "string e.g. 'Part 01' or 'Section 02'",
            "title": "string — section headline",
            "body":  "string (optional) — teaser sentence",
            "num":   "string (optional) — large background number e.g. '01'",
        },
    },
}


@tool(
    name="html_deck_render",
    description=(
        "Render a McKinsey-style deck spec into a self-contained HTML presentation. "
        "Pass the full deck spec as a JSON object with keys: title (string), org (string, optional), "
        "slides (array of slide objects — use html_deck_list_types for the full schema). "
        "Returns a /files/ download URL so the user can open the presentation in the browser."
    ),
)
def html_deck_render(spec: dict, filename: str = "", _config=None) -> ToolResult:
    """Render a deck spec dict to a self-contained HTML presentation file.

    spec     — Dict with keys:
                 title (string), org (string, optional),
                 slides (array of slide objects, each with a 'type' field)
    filename — Optional output filename (e.g. 'report.html'). Defaults to deck title.
               The file is always saved to the server upload dir for immediate download.
    """
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError as e:
            return ToolResult.error(f"Invalid JSON in spec: {e}")

    if not isinstance(spec, dict):
        return ToolResult.error("spec must be a JSON object with 'title' and 'slides' keys.")

    slides = spec.get("slides")
    if not isinstance(slides, list) or not slides:
        return ToolResult.error("spec must have a non-empty 'slides' array.")

    try:
        html_content = _build_html(spec)
    except Exception as exc:
        return ToolResult.error(f"Rendering failed: {exc}")

    # Derive a safe filename from the deck title if not provided
    if not filename:
        raw = spec.get("title", "deck") or "deck"
        filename = re.sub(r"[^\w.\-]", "_", raw)[:80].strip("_") + ".html"
    if not filename.lower().endswith(".html"):
        filename += ".html"
    safe_name = re.sub(r"[^\w.\-]", "_", filename)[:128] or "deck.html"

    # Resolve upload_dir from injected config (same path the server uses for /files/)
    upload_dir: Path | None = None
    if _config is not None:
        upload_dir = getattr(_config.server, "upload_dir", None)
        if upload_dir is None and hasattr(_config, "memory") and _config.memory.data_dir:
            upload_dir = Path(_config.memory.data_dir) / "uploads"
    if upload_dir is None:
        # Fallback: write next to CWD so the path is at least deterministic
        upload_dir = Path.home() / ".hushclaw" / "uploads"

    upload_dir = Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_id = uuid4().hex[:12]
    stored_name = f"{file_id}_{safe_name}"
    dest = upload_dir / stored_name

    try:
        dest.write_text(html_content, encoding="utf-8")
    except OSError as e:
        return ToolResult.error(f"Cannot write file {dest}: {e}")

    rel_url = f"/files/{stored_name}"

    # Build absolute URL if server has a public_base_url configured
    abs_url = ""
    if _config is not None:
        base = str(getattr(_config.server, "public_base_url", "") or "").strip()
        if base:
            abs_url = f"{base.rstrip('/')}{rel_url}"

    payload = {
        "trusted": True,
        "url": rel_url,
        "name": safe_name,
        "file_id": file_id,
        "slides_rendered": len(slides),
        "hint": "Open the URL in a browser → '▶ Present' for slideshow (←/→ keys) | 'Print / Save PDF' for PDF export.",
    }
    if abs_url:
        payload["absolute_url"] = abs_url

    return ToolResult.ok(json.dumps(payload, ensure_ascii=False))


@tool(
    name="html_deck_list_types",
    description=(
        "List all 13 supported slide types with their field schemas. "
        "Call this before composing a deck spec to know what fields each type accepts."
    ),
)
def html_deck_list_types() -> ToolResult:
    lines = []
    for name, info in _SLIDE_SCHEMAS.items():
        lines.append(f"### {name}")
        lines.append(f"Best for: {info.get('best_for', '')}")
        lines.append(f"Fields:\n{json.dumps(info.get('fields', {}), ensure_ascii=False, indent=2)}")
        lines.append("")
    return ToolResult.ok("\n".join(lines))
