// Grade -> color -> pixel, plus the color+SHAPE encoding for accessibility.
//
// Colors are owned by the backend (/grades or data/grades.json) so the map, list,
// and card never drift onto a different palette than the scoring engine. This
// module never hardcodes a grade color; it only looks them up once loaded.
//
// Grade is encoded THREE independent ways so it never relies on color alone
// (WCAG 1.4.1): the letter itself, the fill color, and a per-tier SHAPE. A red
// F and a green A+ differ in hue, in glyph, AND in silhouette.

import { MARKER_SIZE } from "./config.js";

let COLORS = {};
let NA = "#5a636e";

export function setScale(colors, naColor) {
  COLORS = colors || {};
  NA = naColor || NA;
}
export function naColor() { return NA; }

export function colorFor(grade) {
  return (grade && COLORS[grade]) || NA;
}

// Relative luminance (WCAG 2.x).
function luminance(hex) {
  const c = hex.replace("#", "");
  const n = c.length === 3 ? c.split("").map(x => x + x).join("") : c;
  const [r, g, b] = [0, 2, 4].map(i => {
    const v = parseInt(n.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

// Pick the higher-contrast text color for a badge background. The grade palette
// spans dark green to pale yellow to red, so neither black nor white wins across
// all of it -- choosing by ACTUAL contrast ratio (not a luminance cutoff) is what
// keeps the mid-tone greens/yellows legible. Returns the better of dark/white.
const INK = "#0b1220";
export function textOn(hex) {
  return contrast(hex, INK) >= contrast(hex, "#ffffff") ? INK : "#ffffff";
}

// Grade -> tier letter -> shape family.
export function tierOf(grade) {
  if (!grade || grade === "N/A" || grade === "—" || grade === "?") return "na";
  return grade[0];   // "A+"/"A-"/"A" -> "A"
}
const SHAPES = { A: "circle", B: "rounded", C: "hexagon", D: "diamond", F: "triangle", na: "pill" };
export function shapeFor(grade) { return SHAPES[tierOf(grade)] || "pill"; }

// The SVG silhouette for a shape, drawn in a 0..S viewBox. Kept as vector paths
// (not clipped boxes) so the centered letter is never cut off.
function shapePath(shape, S) {
  const m = S * 0.12, a = m, b = S - m;          // inset margin
  const cx = S / 2, cy = S / 2, r = (S - 2 * m) / 2;
  switch (shape) {
    case "circle":   return `<circle cx="${cx}" cy="${cy}" r="${r}"/>`;
    case "rounded":  return `<rect x="${a}" y="${a}" width="${b - a}" height="${b - a}" rx="${S * 0.22}"/>`;
    case "hexagon": {
      const pts = Array.from({ length: 6 }, (_, i) => {
        const ang = Math.PI / 180 * (60 * i - 30);
        return `${(cx + r * Math.cos(ang)).toFixed(1)},${(cy + r * Math.sin(ang)).toFixed(1)}`;
      });
      return `<polygon points="${pts.join(" ")}"/>`;
    }
    case "diamond":  return `<polygon points="${cx},${a} ${b},${cy} ${cx},${b} ${a},${cy}"/>`;
    case "triangle": {
      const t = a + S * 0.04, bot = b + S * 0.02;
      return `<polygon points="${cx},${t} ${b + S * 0.02},${bot} ${a - S * 0.02},${bot}"/>`;
    }
    default:         return `<rect x="${a}" y="${cy - r * 0.62}" width="${b - a}" height="${r * 1.24}" rx="${r * 0.62}"/>`;
  }
}

// A complete badge SVG: shape + centered label, plus a focus ring and an alert
// ring that CSS toggles. `S` is the viewBox size; the caller scales it in CSS.
// The `.badge-fill` class is what map.js recolors in place for the live fade.
export function badgeSVG(label, grade, { size = MARKER_SIZE, alert = false } = {}) {
  const S = size;
  const fill = colorFor(grade === "—" || grade === "?" ? null : grade);
  const fg = textOn(fill);
  const fs = label.length > 1 ? S * 0.42 : S * 0.5;
  return `<svg viewBox="0 0 ${S} ${S}" width="${S}" height="${S}" role="img" aria-hidden="true">
    <g class="ring" style="opacity:0"><circle cx="${S / 2}" cy="${S / 2}" r="${S / 2 - 1}"
       fill="none" stroke="var(--focus)" stroke-width="2"/></g>
    <g class="alert-ring" style="opacity:${alert ? 1 : 0}">
       ${shapePath(shapeFor(grade), S).replace("/>", ` fill="none" stroke="var(--alert)" stroke-width="${S * 0.14}"/>`)}</g>
    <g class="badge-fill" fill="${fill}" stroke="rgba(0,0,0,.35)" stroke-width="1">
       ${shapePath(shapeFor(grade), S)}</g>
    <text x="${S / 2}" y="${S / 2}" fill="${fg}" font-family="var(--font)"
      font-size="${fs}" font-weight="700" text-anchor="middle" dominant-baseline="central"
      style="paint-order:stroke">${label}</text>
  </svg>`;
}

// The letters worth showing in the legend (anchor grades; the full 11 crowd it).
export const LEGEND_GRADES = ["A+", "A", "B", "C", "D", "F"];
