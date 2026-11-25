# SEO final fix report — Djini's Bakehouse

**Date:** 2026-11-25 · **Scope:** the two changes the owner approved after the
release review (`docs/operations/SEO_RELEASE_REVIEW.md`): H1 (keep crawlers
off the pages that act when opened) and font preloading on the home page
only. **State: local changes only — not committed, not pushed, not
deployed.** No price, VAT, payment, checkout, sign-in or unsubscribe logic,
customer data, legal text, product text, address data, Render setting or DNS
record was changed.

---

## Summary

| | Result |
| --- | --- |
| H1 — robots.txt | Closes exactly the two pages that act when opened, each by its own address: `Disallow: /unsubscribe` and `Disallow: /auth/callback` (narrowed at the owner's re-review from `/uns` and `/auth/` — see below) |
| Endpoint protections | Unchanged and proven by tests: a made-up, spent, expired or disabled-account code is refused; a spent or made-up unsubscribe token is refused |
| A person using either page | Unchanged: unsubscribe and sign-in still work in the browser, once |
| Font preload | Home page only. Home keeps its layout-shift fix; other pages no longer preload |
| Typography | Unchanged — same fonts on every page, screenshots compared pixel for pixel |
| Tests (final, after the robots re-review) | Backend 627 passed, 0 failed · frontend 63/63 · build current · robots census 4/4 · the two pages used by a person 6/6 · SEO browser checks 32/32 · smoke 78 pages, no problems (section 4) |

### Robots.txt re-review — narrowed to the two pages

The first version of this fix closed `/uns` (the rule as briefed) and
`/auth/` (added because `/uns` cannot reach the sign-in callback). At the
owner's request both were re-examined against the code:

| | Unsubscribe | Sign-in callback |
| --- | --- | --- |
| The page | `/unsubscribe` — the only route starting `/uns` (`frontend/src/App.jsx:158`) | `/auth/callback` — the only route under `/auth/` (`frontend/src/App.jsx:155`) |
| Every link to it | `/unsubscribe?token=…` — the marketing emails (`backend/app/routers/marketing.py:658`) | `/auth/callback?code=…` and `…?error=…` — the backend's two redirects (`backend/app/routers/auth.py:574`, `:626`) |
| Links from the shop's own pages | none | none |
| What the old rule also closed | `/uns` — every address starting `/uns`, such as a future German `/unsere-backstube` | `/auth/` — any future page under `/auth/`. (The sign-in API is `/api/auth/…`, a different address, open either way) |
| **Finding** | **Too broad** | **Broader than needed** — today it closes only the callback, but it would silently close any page added under `/auth/` |
| **Rule now** | **`Disallow: /unsubscribe`** | **`Disallow: /auth/callback`** |

Each rule is now the page's own address. robots.txt rules match the start
of an address, so each still covers every link to its page, whatever the
query (`?token=`, `?code=`, `?error=`). Narrower forms were considered and
not used:

- `Disallow: /unsubscribe?` (query links only) would leave the bare page
  open (harmless — it does nothing without a token) but would stop covering
  the page if a token were ever passed another way.
- End-anchored rules such as `Disallow: /unsubscribe$` use an extension not
  every crawler honours.

Neither is safer than naming the page.

---

## 1. Changed files

| File | Change |
| --- | --- |
| `backend/app/routers/content.py:120-144` | robots.txt adds `Disallow: /unsubscribe` and `Disallow: /auth/callback` (lines 140–141); the docstring says why, which links they cover, and that the endpoints check their tokens and codes themselves |
| `backend/app/seo.py:106-107` | `Head.preload_fonts` (default off) |
| `backend/app/seo.py:389-392` | The home page's head turns it on |
| `backend/app/seo.py:496-522` | `Shell` keeps the font preload block only when the head asks for it; without the block (or without the markers) the page is served as before |
| `frontend/index.html:37-46` | The three preload tags sit between `<!-- fonts:start -->` and `<!-- fonts:end -->` markers. The fonts, their files and the stylesheet are unchanged |
| `backend/tests/test_api.py:4698-4745` | New: the exact rule list; both pages closed however they are linked; of the pages the app draws, exactly checkout and these two closed; the neighbouring addresses open; every sitemap page, the API, scripts, styles, fonts and photos open (runs without a frontend build) |
| `backend/tests/test_api.py:559-594` | New: the sign-in code exchange refuses a made-up, expired, spent or disabled-account code and takes a fresh one once |
| `backend/tests/test_seo.py:434-447` | New: on the real build, the home page carries the three preloads (the stylesheet's own files) and other pages none |
| `backend/tests/test_seo_shell.py` (new) | Five unit tests of the shell's blocks — no frontend build needed |

Nothing else changed in this round. The release review's other findings
(M2, M3, L1–L12) were not in scope and are unchanged.

---

## 2. H1 — keeping crawlers off the pages that act when opened

**robots.txt as served now:**

```
User-agent: *
Disallow: /admin
Disallow: /account
Disallow: /checkout
Disallow: /unsubscribe
Disallow: /auth/callback

Sitemap: https://djinisbakehouse.com/sitemap.xml
```

**Coverage, verified two ways.** The regression test
(`backend/tests/test_api.py:4698`) reads the served robots.txt with Python's
standard robots parser and checks:

- the rule list is exactly the five lines above;
- `/unsubscribe`, `/unsubscribe?token=…`, `/auth/callback`,
  `/auth/callback?code=…` and `/auth/callback?error=…` are closed;
- of every page the app draws outside the account and the desk (read from
  `App.jsx`), exactly `/checkout`, `/unsubscribe` and `/auth/callback` are
  closed — home, shop, product pages, basket, log-in, sign-up, password
  pages, bulk orders, contact, privacy, terms, refunds and the Impressum all
  stay open;
- the neighbouring addresses `/uns`, `/unsere-backstube`, `/auth`,
  `/auth/other` and `/authors` stay open;
- every sitemap address, the API (`/api/products`, `/api/store`,
  `/api/auth/me`, `/api/marketing/unsubscribe`), scripts, styles, fonts,
  photos, uploads, the favicon and the manifest stay open.

In the browser, a census of 18 page loads as an anonymous visitor (every
request recorded, the served robots.txt applied) found that the only pages
that write anything when they open are exactly `/unsubscribe?token=…`
(`POST /api/marketing/unsubscribe`) and `/auth/callback?code=…`
(`POST /api/auth/exchange`) — both closed to crawlers by the narrowed rules —
that no public page writes anything, and that every public page, and every
request it makes while drawing itself (API, scripts, styles, fonts,
photos), stays open: **4/4**.

**Not a security mechanism.** robots.txt only asks well-behaved crawlers to
stay away. The protections live in the endpoints, unchanged by this round
and now all covered by tests:

| Endpoint | Protection | Test |
| --- | --- | --- |
| `POST /api/marketing/unsubscribe` | The token must match a customer's current link; a spent or made-up token gets the same 404 (no oracle); using it clears consent, adds the suppression and rotates the token, so the link works once; rate-limited | `tests/test_marketing.py:462` (existing) — passes |
| `POST /api/auth/exchange` | The code must match, be under 60 seconds old, belong to an active account, and is cleared on use; rate-limited | `tests/test_api.py:477` (existing, single use) and `:559` (new: made-up, expired, spent, disabled) — pass |

**A person is unaffected** (browsers do not read robots.txt). Checked in a
real browser on the scratch copy of the shop, as a customer would: a fresh
unsubscribe link confirms "You're off the list, <name>", clears the consent
and rotates the link — and the same link a second time is refused; a fresh
sign-in code signs the customer in and lands on the home page with
"Welcome back, <name>" — the code is spent on the server, and the same code
a second time is refused: **6/6** (re-run with the narrowed rules).

**If a page is added later.** A new page that acts the moment it opens would
need its own line here. A new public page cannot be closed by accident: the
test fails if any page the app draws, or any sitemap address, is closed
beyond what the five rules intend.

---

## 3. Font preload — home page only

**How.** `frontend/index.html` still lists the three preloads (so the build
still writes in the fonts' hashed names), now inside a marked block. The
server keeps that block on the home page and removes it — markers included —
everywhere else (`backend/app/seo.py`). The development server (Vite) shows
`index.html` as it is, so in development every page still preloads; only
the built shop behaves as described.

**Served pages (verified on the built shop):**

| Page | Preload tags in the page | How the browser fetched the three fonts |
| --- | --- | --- |
| Home `/` | 3 | through the preloads (`link`), requested with the page itself — 13 ms after navigation began on the local copy |
| Shop `/products` | 0 | when the text first used them (`css`) — 111 ms, once the app had drawn |
| Product page | 0 | when the text first used them (`css`) — 107 ms |
| Shelf `/products?category=…` | 0 | when the text first used them (`css`) |
| Contact | 0 | when the text first used them (`css`) |

Before the change every page requested them through the preloads, 8–13 ms
in (local copy, no throttling — the gap widens on slow connections).

The home page's three preloads are the stylesheet's own hashed files, so
nothing is fetched twice (also a test: `tests/test_seo.py:434`).

**Typography (verified).** On all five pages, before and after the change:
headings in Fraunces 600, body text in Karla, the header's "Bakehouse" in
Fraunces italic, and all three faces loaded. Screenshots taken before the
change and after it (1280×900, fonts settled, reduced motion) are
**identical pixel for pixel** on all five pages: the fonts, their files and
the look are untouched; only *when* product and other pages fetch the fonts
changed.

**Lab performance after the change** — 5 loads per page, median [min–max],
same server and data as the review's measurements, Chromium as a phone on
simulated slow 4G with a 4× CPU slowdown, cold cache. **Lab, not field
data.**

| Page | Preloads on every page (review build) | Home only (this round) |
| --- | --- | --- |
| Home | FCP 2360 · LCP 3172 · CLS 0.035 | FCP 2320 [2304–2380] · LCP 3248 [3192–3488] · **CLS 0.035** — kept |
| Shop | FCP 2268 · LCP 2268 (heading) · CLS 0.035 | **FCP 1792** [1768–1816] · LCP 1792 (heading) · CLS 0.040 |
| Product | FCP 2260 · LCP 3560 · CLS 0.035 | **FCP 1764** [1744–1764] · **LCP 3332** [3316–3344] · CLS 0.035 |
| Pastries shelf | FCP 2316 · LCP 4812 · CLS 0.068 [0.035–0.068] | **FCP 1796** [1784–1824] · **LCP 4504** [4480–4532] · CLS 0.073 [0.073–0.073] |

- **Home:** unchanged within run-to-run noise — the layout-shift fix stays
  (0.035, the announcement bar's share).
- **Product pages:** first paint ≈0.5 s sooner and **LCP 228 ms sooner** —
  the +220 ms the review found is gone; layout shift unchanged.
- **Shop and shelves:** first paint ≈0.5 s sooner, shelf LCP 308 ms sooner;
  the cost is a small font swap back on these pages (layout shift +0.005,
  0.040 and 0.073 — well under the 0.1 that counts as "good").

---

## 4. Test results

### Re-run after the robots.txt re-review (final)

Only `backend/app/routers/content.py` (the two rules and their docstring)
and the robots test in `backend/tests/test_api.py` changed in the
re-review; no frontend file changed, so the production build from the first
pass (`index-D4KrLP6i.js`) is current.

| Check | Result |
| --- | --- |
| Backend suite (`pytest -q`) | **627 passed, 0 failed** (the robots test was rewritten, not added) |
| — robots.txt: exact rules, exact closed pages, neighbours and resources open (`test_api.py:4698`) | passes |
| — sign-in code: made-up, expired, spent, disabled (`test_api.py:559`) | passes |
| — Facebook sign-in incl. single use (`test_api.py:477`) | passes |
| — unsubscribe token: spent, made-up, rotation, suppression (`test_marketing.py:462`) | passes |
| Frontend unit tests (`npm test`) | **63/63** |
| Browser — robots.txt as served by the real server | exactly the five rules |
| Browser — request census with robots.txt applied | **4/4** |
| Browser — the two pages, used by a person | **6/6** |
| Browser — SEO checks (heads, navigation, 404, `HEAD`, sitemap) | **32/32** |
| Browser — every route, every role (smoke) | 78 page loads, no problems |

### First pass (font change and the first robots draft)

All run on this machine against the code of the time and a fresh
production build; browser checks on a scratch copy of the shop (the built
app behind the real server, throwaway database).

| Check | Result |
| --- | --- |
| Backend suite (`pytest -q`) | **627 passed, 0 failed** — 619 before this round, 8 new |
| — robots.txt regression (`test_api.py:4698`, new) | passes |
| — sign-in code: made-up, expired, spent, disabled (`test_api.py:559`, new) | passes |
| — Facebook sign-in incl. single use (`test_api.py:477`, existing) | passes |
| — unsubscribe token: spent, made-up, rotation, suppression (`test_marketing.py:462`, existing) | passes |
| — SEO tests (`test_seo.py`) | **25/25** — the 24 earlier ones still pass, plus the font check |
| — shell unit tests (`test_seo_shell.py`, new) | **5/5** |
| Frontend unit tests (`npm test`) | **63/63** |
| Production build (`npm run build`) | OK — scripts and styles unchanged (`index-D4KrLP6i.js`, `index-DBC8LJ0B.css`); the fonts block is in the built `index.html` with the fonts' hashed names |
| Scope check (names used but never defined) | 95 files, 0 problems |
| Browser — font preload per page | Home 3 preloads (`link`); shop, product, shelf, contact 0 (`css`) |
| Browser — typography | Same fonts on all five pages; screenshots before/after **identical pixel for pixel, 5/5** |
| Browser — request census with robots.txt applied | **4/4** |
| Browser — the two pages, used by a person | **6/6** |
| Browser — SEO checks (heads, navigation, 404, `HEAD`, sitemap) | **32/32** |
| Browser — every route, every role (smoke) | 78 page loads, no problems |
| Browser — design round (search, lightbox, filter sheet, docked bars, contact, desk on a phone) | **85/85** |
| Lab performance | section 3 |

---

## 5. Git diff review

- **This round touched only** `backend/app/routers/content.py`,
  `backend/app/seo.py`, `frontend/index.html`, `backend/tests/test_api.py`,
  `backend/tests/test_seo.py`, the new `backend/tests/test_seo_shell.py` and
  this report — all listed in section 1. The other paths in `git status`
  are the earlier, still-uncommitted SEO round.
- **Protected areas untouched:** no payment, pricing, VAT, orders, checkout,
  basket, sign-in, unsubscribe, marketing, desk, email, SMS, configuration,
  model, schema, migration, environment, `render.yaml`, dependency or lock
  file is in the change set. `marketing.py`, `auth.py`, `Unsubscribe.jsx`
  and `SocialCallback.jsx` are unchanged.
- **No secrets, no database files:** the diff and new files were scanned for
  key patterns — none; no `.db`/`.sqlite`/dump in the change set; the local
  development database was not modified.
- **Line endings:** every file CRLF, like its neighbours.
- **Size:** tracked files now 15 changed, +352 / −132 (the earlier round
  was +251 / −132); new files `seo.py`, `test_seo.py`, `test_seo_shell.py`
  and three reports.
- **robots.txt against production:** the only rule changes are
  `Disallow: /api/` removed (the earlier round) and `Disallow: /unsubscribe`
  and `Disallow: /auth/callback` added — no trace of `/uns` or `/auth/` is
  left.

---

## 6. Remaining concerns

1. **Resolved at the re-review:** the broad `/uns` and `/auth/` rules are
   gone; each page is closed by its own address (section "Robots.txt
   re-review").
2. **A future page that acts the moment it opens** would need its own
   robots.txt line; a future public page cannot be closed by accident (the
   test guards it).
3. **robots.txt is advisory.** Mail-security scanners and other tools that
   ignore it can still open an unsubscribe link and so complete the
   one-click unsubscribe — a property of the one-click design that predates
   the SEO work; a confirm button would be a product decision.
4. **Still open from the release review, outside this round's scope:** M2
   (confirm `PUBLIC_BASE_URL` in Render), M3 (no CI; `tests/test_seo.py`
   skips without a frontend build), and L1–L12.
5. **Earlier reports.** `SEO_IMPLEMENTATION_REPORT.md` describes the font
   preload as on every page and the earlier robots.txt rules; this report
   supersedes those two points. `SEO_RELEASE_REVIEW.md` is kept as written:
   its H1 and M1 are what this round answers.
6. **Unverified:** how Google actually crawls after a deploy, field data
   from real visitors, real phones.

Nothing has been committed or deployed. Waiting for the owner's approval.
