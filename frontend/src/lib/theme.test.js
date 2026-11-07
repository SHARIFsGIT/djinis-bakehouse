import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_BRAND_COLOR,
  DEFAULT_HERO_IMAGE,
  applyBrandColor,
  brandCss,
  cacheHeroImage,
  cachedHeroImage,
  cachedBrandColor,
  hexToOklch,
  isValidHexColor,
} from "./theme.js";

/* The conversion is anchored to OKLab's published reference values for the
   sRGB primaries and white — loose enough to survive rounding, tight enough
   that a broken matrix cannot pass. */
const close = (actual, expected, tolerance, label) =>
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `${label}: ${actual} vs ${expected}`,
  );

test("hexToOklch matches the published sRGB anchors", () => {
  const red = hexToOklch("#ff0000");
  close(red.l, 0.628, 0.01, "red L");
  close(red.c, 0.2577, 0.01, "red C");
  close(red.h, 29.2, 2, "red H");

  const green = hexToOklch("#00ff00");
  close(green.l, 0.866, 0.01, "green L");
  close(green.c, 0.2948, 0.01, "green C");
  close(green.h, 142.5, 2, "green H");

  const blue = hexToOklch("#0000ff");
  close(blue.l, 0.452, 0.01, "blue L");
  close(blue.c, 0.3132, 0.015, "blue C");
  close(blue.h, 264.05, 2, "blue H");

  const white = hexToOklch("#ffffff");
  close(white.l, 1.0, 0.005, "white L");
  close(white.c, 0, 0.002, "white C");

  // The logo's own burgundy: a red-wine hue with the chroma the shipped
  // theme's seats carry (the designer rounded the hue to 12; the pick's own
  // hue measures ≈16.5).
  const burgundy = hexToOklch(DEFAULT_BRAND_COLOR);
  close(burgundy.h, 16.5, 4, "burgundy H");
  assert.ok(burgundy.c > 0.1 && burgundy.c < 0.17, `burgundy C: ${burgundy.c}`);
});

test("hexToOklch keeps hue and chroma in range for any pick", () => {
  for (const hex of ["#000000", "#808080", "#7f2230", "#1e7d74", "#b23a5e", "#ffffff"]) {
    const { l, c, h } = hexToOklch(hex);
    assert.ok(l >= 0 && l <= 1, `${hex} L`);
    assert.ok(c >= 0, `${hex} C`);
    assert.ok(h >= 0 && h < 360, `${hex} H`);
  }
});

test("isValidHexColor accepts only padded-free six-digit hex codes", () => {
  assert.equal(isValidHexColor("#7f2230"), true);
  assert.equal(isValidHexColor("#1E7D74"), true);
  assert.equal(isValidHexColor(" #1e7d74 "), true); // trimmed, as the server normalizes
  for (const bad of ["7f2230", "#7f22", "#7f22304", "#7f2g30", "red", "", null, undefined]) {
    assert.equal(isValidHexColor(bad), false, String(bad));
  }
});

test("brandCss re-seats both themes and caps chroma at the shipped seats", () => {
  const css = brandCss("#2f5fa8");
  // Day, the navbar's explicit midnight, and the pre-toggle dark fallback.
  assert.ok(css.includes(":root{"), "light block");
  assert.ok(css.includes('[data-theme="midnight"]{'), "midnight block");
  assert.ok(css.includes("@media (prefers-color-scheme: dark)"), "dark fallback");
  // Every seat keeps its lightness scaffold — the contrast contract.
  assert.ok(css.includes("--color-primary:oklch(45% "), "primary lightness");
  assert.ok(css.match(/--color-secondary:oklch\((54|60)% /), "secondary lightness");

  // A neon pick (blue's chroma ≈ 0.31) is tamed to the seats' own ceilings.
  const neon = brandCss("#0000ff");
  for (const chroma of neon.matchAll(/oklch\(\d+% ([\d.]+) /g)) {
    assert.ok(Number(chroma[1]) <= 0.16, `chroma cap: ${chroma[1]}`);
  }

  // Nothing to override for a bad pick or no pick — the shipped burgundy is
  // the absence of an override (null), not a colour to re-apply.
  assert.ok(brandCss(DEFAULT_BRAND_COLOR).includes(":root{"), "explicit burgundy is still a pick");
  assert.equal(brandCss("not-a-color"), "");
  assert.equal(brandCss(null), "");
});

test("hero cache stores only a custom photo, never the shipped default", () => {
  // node:test has no DOM — stub the storage the helpers read and write.
  const store = new Map();
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };

  cacheHeroImage("/static/uploads/site/hero-abc.jpg");
  assert.equal(cachedHeroImage(), "/static/uploads/site/hero-abc.jpg");

  cacheHeroImage(DEFAULT_HERO_IMAGE); // back to the shipped photo
  assert.equal(cachedHeroImage(), null);

  cacheHeroImage(null);
  assert.equal(cachedHeroImage(), null);
  assert.equal(cachedBrandColor(), null); // unset brand stays unset
});

test("brand cache round-trips a valid pick and refuses garbage", () => {
  const store = new Map();
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  // A minimal document — just enough of a head for the override sheet.
  const elements = {};
  globalThis.document = {
    getElementById: (id) => elements[id] ?? null,
    createElement: () => ({ textContent: "", remove() { delete elements[this.id]; } }),
    head: { appendChild: (el) => { elements[el.id] = el; } },
    querySelector: () => null,
  };

  applyBrandColor("#1E7D74"); // as the server normalizes it, uppercase
  assert.equal(cachedBrandColor(), "#1e7d74");
  assert.ok(elements["brand-colour-override"]?.textContent.includes("oklch("));

  // A value that isn't a hex code is a reset, not a pick — the cache and
  // the override both come off.
  applyBrandColor("oops");
  assert.equal(cachedBrandColor(), null);
  assert.equal(elements["brand-colour-override"], undefined);
});
