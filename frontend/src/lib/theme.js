/* Shop appearance: the brand colour the baker picks from Settings, and the
   home-page photo's last-known URL. Both are per-browser caches in
   localStorage — never a source of truth (the server is), just the fastest
   answer so the first paint is already the right colour and the right photo
   instead of a flash of the shipped defaults. */

const BRAND_KEY = "bakehouse.brand-color";
const HERO_KEY = "bakehouse.hero-image";
const STYLE_ID = "brand-colour-override";

/** The burgundy the logo wears — the browser-chrome tint when no pick exists. */
export const DEFAULT_BRAND_COLOR = "#7f2230";

/** The hero photo shipped with the site, as the backend names it. */
export const DEFAULT_HERO_IMAGE = "/static/site/baker.jpg";

/* localStorage can throw (private mode, storage full, disabled) — the cache
   is a nicety, never a dependency, so every touch is wrapped. */
const readLocal = (key) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};
const writeLocal = (key, value) => {
  try {
    if (value === null || value === undefined) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {}
};

/** A colour is exactly "#rrggbb" — six hex digits, nothing else. */
export const isValidHexColor = (value) =>
  /^#[0-9a-f]{6}$/i.test(String(value ?? "").trim());

/* ------------------------------------------------------------------ */
/* sRGB → OKLCH (Björn Ottosson's OKLab reference matrices)            */
/* ------------------------------------------------------------------ */

/** "#rrggbb" → { l: 0..1, c: 0.., h: 0..360 } — the picked colour in the
 *  space the theme's seats are written in. Pure and DOM-free so it can be
 *  unit-tested under node. */
export function hexToOklch(hex) {
  const value = String(hex ?? "").trim();
  const r = parseInt(value.slice(1, 3), 16) / 255;
  const g = parseInt(value.slice(3, 5), 16) / 255;
  const b = parseInt(value.slice(5, 7), 16) / 255;

  const linear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const [lr, lg, lb] = [linear(r), linear(g), linear(b)];

  const l = 0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb;
  const m = 0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb;
  const s = 0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb;
  const l_ = Math.cbrt(l);
  const m_ = Math.cbrt(m);
  const s_ = Math.cbrt(s);

  const lightness = 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_;
  const a = 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_;
  const b2 = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_;

  const chroma = Math.sqrt(a * a + b2 * b2);
  let hue = (Math.atan2(b2, a) * 180) / Math.PI;
  if (hue < 0) hue += 360;
  return { l: lightness, c: chroma, h: hue };
}

/* The theme keeps its own lightness/chroma scaffolding — the seats' L values
   are what keep cream text readable on every button and wine links readable
   on every background — so a pick re-seats the hue (and a tamed share of the
   picked chroma) rather than swapping the colour wholesale. Caps mirror the
   shipped burgundy relationship (primary 0.16, secondary ~0.14 of that). */
const LIGHT_PRIMARY_C = 0.16;
const LIGHT_SECONDARY_C = 0.14;
const NIGHT_PRIMARY_C = 0.15;
const NIGHT_SECONDARY_C = 0.135;

const seat = (lightness, chroma, hue) =>
  `oklch(${lightness}% ${chroma.toFixed(3)} ${hue.toFixed(1)})`;

/** The style sheet text that re-tints the theme's brand seats for one pick.
 *  The midnight block is written twice — once for the explicit
 *  [data-theme="midnight"] the navbar toggle sets, once for the
 *  prefers-color-scheme fallback before any toggle runs — matching how the
 *  base theme itself declares both. Empty string when there is nothing to
 *  override (the shipped burgundy stays). */
export function brandCss(hex) {
  if (!isValidHexColor(hex)) return "";
  const { c, h } = hexToOklch(hex);
  const light =
    `--color-primary:${seat(45, Math.min(c, LIGHT_PRIMARY_C), h)};` +
    `--color-secondary:${seat(54, Math.min(c * 0.875, LIGHT_SECONDARY_C), h)}`;
  const night =
    `--color-primary:${seat(45, Math.min(c, NIGHT_PRIMARY_C), h)};` +
    `--color-secondary:${seat(60, Math.min(c * 0.9, NIGHT_SECONDARY_C), h)}`;
  return (
    `:root{${light}}` +
    `[data-theme="midnight"]{${night}}` +
    `@media (prefers-color-scheme: dark){:root:not([data-theme]){${night}}}`
  );
}

/* What is currently painted — skips rewriting the sheet for the same pick. */
let appliedColor = null;

/** Paint the shop in the baker's pick. `null` (or anything not a hex code)
 *  takes the override off and restores the shipped burgundy. Persists to the
 *  local cache unless asked not to (the Settings tab previews without
 *  committing). Safe to call repeatedly. */
export function applyBrandColor(hex, { persist = true } = {}) {
  const color = isValidHexColor(hex) ? hex.trim().toLowerCase() : null;
  if (persist) writeLocal(BRAND_KEY, color);
  if (color === appliedColor) return;
  appliedColor = color;

  let style = document.getElementById(STYLE_ID);
  const css = brandCss(color);
  if (!css) {
    style?.remove();
  } else {
    if (!style) {
      style = document.createElement("style");
      style.id = STYLE_ID;
      document.head.appendChild(style);
    }
    style.textContent = css;
  }
  // The browser chrome follows the shop: the pick when one exists, the
  // logo's burgundy when it doesn't.
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", color ?? DEFAULT_BRAND_COLOR);
}

/** A look before it is saved — paints exactly like applyBrandColor but
 *  leaves the local cache alone, so an abandoned experiment never outlives
 *  the visit. */
export const previewBrandColor = (hex) => applyBrandColor(hex, { persist: false });

/** The last server-confirmed pick, for the pre-paint boot script. */
export const cachedBrandColor = () => {
  const value = readLocal(BRAND_KEY);
  return isValidHexColor(value) ? value : null;
};

/* ------------------------------------------------------------------ */
/* The home-page photo                                                 */
/* ------------------------------------------------------------------ */

/** The hero the server last confirmed — null means "no custom photo". */
export const cachedHeroImage = () => {
  const value = readLocal(HERO_KEY);
  return value && value !== DEFAULT_HERO_IMAGE ? value : null;
};

/** Remember the server's answer. The shipped default is deliberately not
 *  cached: painting it before /store answers is exactly the stale-photo
 *  flash this cache exists to prevent (the shipped photo IS the flash). */
export const cacheHeroImage = (url) =>
  writeLocal(HERO_KEY, url && url !== DEFAULT_HERO_IMAGE ? url : null);
