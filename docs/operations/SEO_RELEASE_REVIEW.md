# SEO release review — Djini's Bakehouse

**Date:** 2026-11-25 · **Reviewed:** the uncommitted SEO round on branch
`ui/auth-restore-admin-orders`, on top of `35aad41` (the release running in
production) — 13 modified, 2 deleted and 3 new files (section 9).
**Mode:** read-only. Nothing in the implementation was changed; this document
is the only file the review wrote. Nothing was committed, pushed or deployed;
no Render setting, DNS record or production data was touched; every request
to the live site was a read-only `GET`/`HEAD`.

---

## Verdict

**Not safe to release as it stands — safe to release once H1 is fixed.**

- **No critical issues.**
- **One high-priority issue (H1):** opening `/api/` to crawlers re-opens a
  path the old robots.txt happened to close: a crawler that renders a leaked
  one-click unsubscribe link can complete the unsubscribe. The fix is two
  lines in robots.txt plus a test (section 2).
- **Prices and VAT are handled correctly.** No price is published in
  structured data while VAT is added at checkout; with VAT-inclusive pricing
  switched on, the published price is exactly the gross shelf price the page
  shows (checked against all 26 live products).
- Everything else checked passes, apart from three medium items (one
  performance trade-off for the owner to decide, two process points) and
  minor wording and hygiene items.

---

## How the review was done

| Evidence | What it covered | Result |
| --- | --- | --- |
| Line-by-line reading of the diff and the three new files | All seven areas of the review brief | Findings below |
| **Live-catalogue replica** — the new server code run over a copy of the live shop's public data (26 products, 4 shelves, store settings, read from the public API today, live product ids kept) with the production base address | Every head, the sitemap, the structured data, private pages — what production will serve after a deploy | **22/22 on SQLite, 22/22 on PostgreSQL 18** (production's engine) |
| Backend suite (`pytest -q`) | Everything, incl. 24 SEO tests | **619 passed**, 0 failed |
| Frontend unit tests (`npm test`) | Wording rules shared with the server | **63/63** |
| Production build (`npm run build`) | Markers, preloads, bundle | OK, reproducible (`index-D4KrLP6i.js`) |
| Scope check (names used but never defined) | 95 frontend files | 0 problems |
| **Browser census** on the built app behind the real server — every request and console line on 18 page loads as an anonymous visitor | What a crawler's render asks of the server | 7/7 |
| SEO browser checks | Heads before/after the app runs, navigation, 404s, `HEAD`, sitemap | **32/32** |
| Earlier rounds' browser suites on the final build | Existing features | section 5, "Regression and security" |
| Raw-byte redirect probes | Open redirects, loops | all same-site |
| Lab loads, 5 runs per page, live site and final build | Performance | section 6 |
| Live site, read-only | robots.txt, sitemap, `HEAD`, `/index.html`, logo, photos | sections 2, 4 and 5 |

---

## 1. Critical issues

None.

---

## 2. High-priority issues

### H1 — Crawlers that render pages can now complete the one-click unsubscribe

**Where.** `backend/app/routers/content.py:119-131` (robots.txt no longer
disallows `/api/`; it never disallowed `/unsubscribe` or `/auth/`) ·
`frontend/src/pages/Unsubscribe.jsx:19-37` (posts the token as soon as the
page opens — by design, GDPR one-click) · `backend/app/routers/marketing.py:877-899`
(clears the customer's marketing consent and rotates their link) ·
`frontend/src/pages/SocialCallback.jsx:27-43` (exchanges the one-time sign-in
code as soon as the page opens).

**What happens.** The browser census confirms that `/unsubscribe?token=…`
sends `POST /api/marketing/unsubscribe` and `/auth/callback?code=…` sends
`POST /api/auth/exchange` on load — the only writes any of the 18 pages
loaded makes without a click (pages behind log-in are out of a crawler's
reach). Google renders pages in Chromium and has said its renderer handles
XHR `POST` requests. Before this release, `Disallow: /api/` stopped every
robots-respecting renderer from reaching the API, so these calls failed for
crawlers. Now nothing stops them. If an unsubscribe link ever becomes public
(a newsletter forwarded to a public list or archive), a crawler that renders
it unsubscribes that customer and rotates their link.

**Mitigating, not closing.** Both pages now carry `noindex, nofollow` in the
first answer (`backend/app/seo.py:87`, `:372`), so nothing would be indexed,
and Google's documentation says `noindex` *may* stop it from rendering —
but its wording is hedged, and a published third-party test reports Googlebot
still running a page's fetch calls on `noindex` pages. Other crawlers make no
such promise. The sign-in code is single-use and expires after 60 seconds
(`backend/app/routers/auth.py:102`, `:622-626`), so that half is very
unlikely; the unsubscribe half is the real one.

**Likelihood / impact.** Low likelihood (the links live in emails). Impact: a
customer's marketing consent changes without their action — a change to
customer data, introduced by this release.

**Fix (not made — this review is read-only).** Add `Disallow: /unsubscribe`
and `Disallow: /auth/` to robots.txt (both pages are already `noindex` and
need no crawling; no public page needs them), with a test. Optionally also
`Disallow: /api/marketing/` and `Disallow: /api/auth/`: the census shows no
public page calls either while it renders. Mail-security scanners that ignore
robots.txt are a separate, pre-existing exposure of the one-click design;
whether to add a confirm button is a product decision.

---

## 3. Medium-priority issues

### M1 — Font preload: real network contention, a real CLS win — the owner's call

`frontend/index.html:41-43`. The three preloaded fonts (114 KB) now download
alongside the app (169 KB gzipped) instead of after it. Measured on the same
server and data (lab, slow-4G phone profile):

| Page | CLS | First paint (FCP) | LCP |
| --- | --- | --- | --- |
| Home | **0.163 → 0.035** | +452 ms | −452 ms |
| Shop (`/products`) | 0.040 → 0.035 | +492 ms | — (largest element is the heading) |
| Product page | 0.035 → 0.035 | +504 ms | **+220 ms** |

Nothing is wasted: the census shows every page fetches exactly the three
files, once each, and Chrome reports no "preloaded but not used" warning; all
three faces render above the fold on every page (the header's italic
"Bakehouse"). But only the home page gains; product pages pay without a CLS
benefit. **Options:** keep as is (home is the main landing page); or preload
only on the home page — the server already writes each page's head and could
carry the three links there alone; or revert (three lines). On faster
connections the cost should fall well under 100 ms — an estimate, not
measured.

### M2 — Every canonical address depends on `PUBLIC_BASE_URL` (or the proxy)

`backend/app/security.py:160-170`. Canonical addresses, `og:url`, structured
data ids and the sitemap all use `site_base()`: `PUBLIC_BASE_URL` when set,
otherwise the address the request arrived on (Render starts uvicorn with
proxy headers trusted, `render.yaml:23`). The live robots.txt shows
`https://djinisbakehouse.com`, so production resolves correctly today — but
whether `PUBLIC_BASE_URL` is set cannot be seen from outside. The mechanism
predates this release; this release makes far more depend on it. **Owner:**
confirm `PUBLIC_BASE_URL=https://djinisbakehouse.com` in Render → Environment.

### M3 — Nothing guards the SEO block automatically

- There is no CI (`.github/` absent); Render builds and serves without
  running tests (`render.yaml:22-23`).
- `tests/test_seo.py` skips itself entirely when no frontend build is present
  (`backend/tests/test_seo.py:32-34`).
- If the markers ever disappeared from the built `index.html` (for example a
  future HTML minifier stripping comments), the server would silently serve
  the plain shell for every address (`backend/app/seo.py:500`, `:503`) — no
  error, no log line, and no per-page titles, canonicals, robots rules or
  structured data (the status codes would still be right: they come from the
  head, `backend/app/__init__.py:632-633`).

**Recommendation:** keep "build, then `pytest`" in the release steps (the
implementation report's checklist has it), and log a warning at start-up when
the markers are missing. Not a release blocker.

---

## 4. Low-priority issues

| # | Issue | Where |
| --- | --- | --- |
| L1 | Shelf descriptions read "… Blueberry Cheese Bread **and** French Baguette **and** more" — a doubled "and" in every shelf with more than three bakes | `backend/app/seo.py:335-338` |
| L2 | Search pages (`?search=`) say `noindex, follow` *and* name the shelf as canonical — mixed signals; harmless, but the canonical is better left out on a `noindex` page | `backend/app/seo.py:326-353` |
| L3 | *(pre-existing)* `/index.html` is served as the raw build file: no per-page head, no CSP header, no `no-cache` (confirmed on the live site); the app then shows its not-found page with status 200. Nothing links to it | `backend/app/__init__.py:605-607` |
| L4 | *(pre-existing pattern)* After an in-app move, `og:*` tags and the meta description keep the landing page's (or the last product's) values; only canonical/robots/JSON-LD are removed. No effect on crawlers, which load each address fresh | `frontend/src/App.jsx:97-100`, `frontend/src/pages/ProductDetail.jsx:62-69` |
| L5 | The `/bulk` description repeats the page's own promise "we quote within a working day" (from `BulkOrder.jsx:94`) — a service commitment now in search results; the owner should confirm it, like the PENDING item 18 claims | `backend/app/seo.py:428` |
| L6 | A `?page=N` past the last page names itself canonical in the first answer while the app shows the last real page (documented in the implementation report; never linked) | `backend/app/seo.py:348-349` |
| L7 | The shop's pager is buttons, not links — harmless today (every shelf fits on one page; every bake is in the sitemap) | `frontend/src/components/Pager.jsx:12`, `:24` |
| L8 | *(pre-existing)* The default share image is 550×454 px — below the 1200×630 large-card size, so home/shelf/contact links preview small. Product pages use their own 1200×960 photos | `backend/app/seo.py:37` |
| L9 | `HEAD` on an API path answers `405` with `Allow: GET` even where the route is POST-only — cosmetic | `backend/app/__init__.py:602-603` |
| L10 | Page bodies are drawn by JavaScript; crawlers that do not render (some previewers, smaller engines) see only the server-written head. By design | — |
| L11 | When `TAX_MODE=inclusive` switches the offer on, it will lack `shippingDetails` and `hasMerchantReturnPolicy` — Search Console will list these as non-critical warnings | `backend/app/seo.py:273-287` |
| L12 | Outside the SEO scope: `PENDING.md` also records that items 5 and 6 "read as done", based on today's live public settings — the owner should confirm | `PENDING.md:16-21` |

---

## 5. Verified passing checks

### Server-side SEO correctness

| Check | Evidence |
| --- | --- |
| First response carries the right title, description, canonical, robots rule and structured data | Replica: all 37 public addresses (home, shop, 4 shelves, 26 products, 5 standing pages) — unique titles, unique descriptions ≤160 characters, self-canonical, `og:url` = canonical, one `<title>` each |
| Metadata per public route | Every route in `App.jsx` answers 200 (test reads the routes); standing pages' titles and descriptions as in the implementation report |
| Client-side navigation | Server tags survive the app's first render on 5 landing pages; canonical/robots/JSON-LD removed at the first in-app move; tab titles match the server's; the not-found page carries exactly one `noindex`, also after an in-app wrong turn (L4 is the remaining cosmetic gap) |
| Unknown routes are real 404s | `/no-such-page`, `/products/no-such-bake`, `/products/a/b`, `/contact/extra`, hidden bakes — 404 with `noindex`; the shop's own not-found page still shows |
| Redirects | Trailing slash → one 301 to the same-site address, query kept; raw-byte probes `//evil.example/`, `///evil.example/`, `/\evil.example/` all redirect to same-site paths (`/evil.example`, `/%5Cevil.example`); no 301 leads to another 301 |
| `HEAD` | Same status and headers as `GET`, empty body through the real server; API routes keep answering `HEAD` with 405 as before |

### Product indexing and security

| Check | Evidence |
| --- | --- |
| Googlebot can reach all public product content | robots.txt allows `/`, `/assets/` (scripts, styles), `/static/` (photos) and `/api/`; every API call a public page makes on load is a `GET` that answers 200 (census) |
| Public API data available for rendering | Home: store, shelves, featured, testimonials, coming-soon; shop and shelves: products; product page: the product; nothing rate-limited (limits cover only actions) |
| API answers never indexed on their own | `X-Robots-Tag: noindex` on every `/api/` answer (`backend/app/__init__.py:540-541`) |
| Private routes stay private | `/admin`, `/account`, `/checkout` disallowed in robots.txt and `noindex, nofollow`; `/cart`, `/login`, `/register`, `/forgot-password` `noindex, follow`; `/reset-password`, `/unsubscribe`, `/auth/callback` `noindex, nofollow`; none in the sitemap; desk API `no-store` unchanged |
| No sensitive data in heads or structured data | Replica scan of every head: no stock counts, VAT classes, costs, margins, tokens, ingredients or internal ids; tokens in `?token=` never echoed (`og:url` drops the query); only the shop's own published contact details appear (owner decision 6) |
| robots.txt blocks nothing important | Scripts, styles, photos and the catalogue API are open; the three disallowed prefixes hold no public resource |
| No public page writes anything while it renders | Census of 14 public pages: only `GET` requests (H1 covers the two token pages) |

### Sitemap

| Check | Evidence |
| --- | --- |
| Every URL vs real routes and live records | Replica: exactly 37 addresses = 7 standing pages + 4 shelves + all 26 live bakes; nothing missing, nothing extra |
| Status, canonical, trailing slash | Every address answers 200 and names itself canonical; only the home address ends in `/` |
| No duplicates | 37 unique |
| No private or `noindex` pages | None; the Impressum joins only once published |
| Syntax and type | Parses as XML; `Content-Type: application/xml`; image namespace declared; each bake carries its live photo's absolute address |

### Product structured data, prices and VAT

| Check | Evidence |
| --- | --- |
| Shipped mode (`TAX_MODE=exclusive`, VAT added at checkout) | Product pages carry **only** a `BreadcrumbList`: no `Product`, no `Offer`, no price anywhere in the markup (replica, all 26) — so no net price can appear in search results as if it were the whole price |
| Inclusive mode (simulated) | Every `Offer.price` = the live `current_price` to the cent, `EUR`, `InStock`/`OutOfStock` from stock, `valueAddedTaxIncluded: true`; sale prices carried (7 bakes on sale) — the same figure the page shows (`PriceTag.jsx:5`) and checkout charges (`backend/app/pricing.py:14`, `backend/app/vat.py:284`) |
| Currency | Markup and storefront read the same `stripe_currency` with the same EUR fallback (`backend/app/seo.py:170-174`, `backend/app/routers/marketing.py:76`) |
| No fake reviews or ratings | Stars only from approved reviews, exactly the page's numbers; 0 live reviews → no rating anywhere |
| Breadcrumbs and canonical consistent | Shop → shelf → bake; names and addresses match the page's breadcrumb and the canonical (all 26) |
| Organisation markup | `OnlineStore` with the published email, phone and address; logo 256×256 px (Google's minimum is 112) |

### Performance

| Check | Evidence |
| --- | --- |
| Build output | Main bundle 549 kB (169 kB gzipped), essentially unchanged by this round; the three preloads resolve to the stylesheet's own hashed font files |
| Image priority and lazy loading | Home hero and product photo `fetchpriority="high"`; first two shop cards eager + high; everything else lazy |
| CLS | No regression: lower on every page measured (sections 3 and 6) |
| LCP | Lower on the home page (−452 ms) and shelves (−132 to −248 ms); **+220 ms on product pages**, from the font preload (M1) |

### Regression and security

| Check | Evidence |
| --- | --- |
| Payment, checkout, shipping, VAT, auth, admin logic untouched | None of their files is in the change set; the shared app file changes only add a response header, the page catch-all and `HEAD` |
| Links the backend sends customers back to still answer 200 | Stripe/PayPal returns (`/account/orders/{id}?…`), password reset, unsubscribe, sign-in callback |
| No secrets, no database files | Scanned the diff and new files for key patterns: none; no `.db`/`.sqlite`/dump in the change set; the local dev database was not modified |
| Existing features | Backend 619/619, frontend 63/63; the earlier rounds' browser suites, re-run on the final build: smoke 78 page loads with no problems · design 85/85 · launch round 83/83 · small screens 65/65 · admin orders 40/40 · payment journey 40/40 (stand-in payment pages, no real payment) · payment states 8/8 · sign-in fixes 28/28 · restored sign-in pages 64/64 · sign-in journey 27/27 · text sign-in 29/29 · phone round 92/93 on a fresh server — its one failure ("Due next" labels) depends on the dates of scratch orders and fails identically in the previous release's baseline. (Run straight after the other sign-in rounds it also hit the password-reset rate limit, 429 — the limiter working, not a fault; alone it passes.) |

---

## 6. Performance — lab only

All numbers are **lab** measurements: Chromium as a 412×915 phone, simulated
slow 4G (1.6 Mbps, 150 ms), 4× CPU slowdown, cold cache, median [min–max] of
5 loads. **No field data could be checked**: Google's public PageSpeed API
refused the anonymous request (daily quota, HTTP 429); the owner can see field
data, if any exists, at pagespeed.web.dev or in Search Console.

| Page | Live site today (`35aad41`) | Final build (scratch copy) |
| --- | --- | --- |
| Home | FCP 1984 · LCP 3936 [3712–4352] · **CLS 0.177** [0.080–0.177] | FCP 2360 · LCP 3172 [3052–3548] · **CLS 0.035** [0.035–0.035] |
| Shop | FCP 1908 · LCP 6056 · CLS 0.053 | FCP 2268 · LCP 2268 (heading) · CLS 0.035 |
| Product | FCP 1880 · LCP 4104 · CLS 0.053 | FCP 2260 · LCP 3560 · CLS 0.035 |
| Pastries shelf | FCP 1912 · LCP 5972 · CLS 0.090 | FCP 2316 · LCP 4812 · CLS 0.068 [0.035–0.068] |

The two columns are **not like-for-like** (Render and Cloudflare vs a local
server, live photos of ≈240 KB vs demo photos), so read only the direction:
CLS is lower on every page; first paint is later (the font preload, M1). The
like-for-like comparison — same server, same data, one change at a time —
is the table in M1, plus the shelf photos: first-row priority cut shelf LCP
by 248 ms (pastries) and 132 ms (breads). The remaining layout shift
(≈0.035) is the announcement bar, unchanged by this release. The live run
overlapped a local browser session; its figures match the isolated live run
made earlier today within about 10 %.

---

## 7. German market compliance — technical findings, not legal conclusions

**Technical facts (verified):**

- In the shipped mode, shelf, card and product pages show the **net** price
  (`current_price`) with no VAT note beside it; 7 % is added in the basket
  and at checkout (`backend/app/pricing.py:10-13`, `PriceTag.jsx:5`,
  `ProductDetail.jsx:484`).
- No delivery-cost note sits beside the price; the home page's promise row
  states free delivery above the threshold (`Home.jsx:40`); delivery is
  charged in the basket.
- This release publishes **no price** in structured data in that mode, so
  search results cannot present a net price as the whole price. With
  VAT-inclusive pricing (`TAX_MODE=inclusive`, PENDING item 9), the price
  markup appears and equals the gross shelf price.
- The Impressum is not published (live `legal_notice_published: false`); it
  stays `noindex` and out of the sitemap until it is.
- Every product shows ingredients and allergens; their accuracy was not
  checked.

**Questions for the owner's legal or tax adviser** (no conclusion is drawn
here, and nothing was changed): whether net shelf prices meet German
consumer-pricing rules (PAngV total prices); whether a VAT and delivery-cost
note is needed beside prices; whether any product sold by weight or volume
needs a unit price; what the Impressum must contain.

---

## 8. Unverified checks

| Check | Why not | How the owner can verify |
| --- | --- | --- |
| Google actually rendering and indexing the pages | Needs a deploy and Search Console | URL Inspection → Test live URL (implementation report, section 9) |
| Rich Results Test | Needs a public URL running this release | search.google.com/test/rich-results after deploy |
| Field data (Core Web Vitals from real visitors) | PageSpeed API refused (429); likely too little traffic for any | pagespeed.web.dev, Search Console |
| `PUBLIC_BASE_URL` in Render | Owner-only | Render → Environment (M2) |
| Whether Googlebot would send the unsubscribe `POST` | Documented behaviour is hedged; not testable from here | Moot once H1 is fixed |
| Cloudflare/Render treatment of Googlebot | Edge settings are owner-only | URL Inspection after deploy |
| Real phones | Emulation only | PENDING item 14 |

---

## 9. Files in the change set

Modified: `PENDING.md`, `backend/app/__init__.py`, `backend/app/routers/content.py`,
`backend/tests/test_api.py`, `frontend/index.html`, `frontend/src/App.jsx`,
`frontend/src/components/PhotoTile.jsx`, `frontend/src/components/ProductCard.jsx`,
`frontend/src/lib/titles.js`, `frontend/src/lib/titles.test.js`,
`frontend/src/pages/NotFound.jsx`, `frontend/src/pages/ProductDetail.jsx`,
`frontend/src/pages/Products.jsx`. Deleted: `frontend/public/robots.txt`,
`frontend/public/sitemap.xml`. New: `backend/app/seo.py`,
`backend/tests/test_seo.py`, `docs/operations/SEO_IMPLEMENTATION_REPORT.md` —
and this review. Line endings are consistent (CRLF, like their neighbours).

---

## 10. Remaining owner decisions

1. **H1:** approve the robots.txt fix (`Disallow: /unsubscribe`, `Disallow: /auth/`,
   optionally the two API write paths) — or knowingly accept the risk.
2. **M1:** font preload — keep, home page only, or revert.
3. **M2:** confirm `PUBLIC_BASE_URL` in Render.
4. **VAT-inclusive prices** with the adviser (PENDING item 9) — this also
   switches on price markup.
5. **Impressum** (PENDING item 3).
6. **The published address and phone in the home page's structured data.**
   They are already on the contact page; the markup makes them
   machine-readable. If the address is a private home — and especially with a
   Google Business Profile that hides it — decide whether it should appear.
7. **Wording:** the `/bulk` promise (L5) and the PENDING item 18 claims.
8. **A larger default share image** (L8), if wanted.

Until the owner approves, nothing will be changed, committed or deployed.

---

**Sources consulted for Google's behaviour:** Search Engine Land,
"[Googlebot renders … content driven by XHR POST](https://searchengineland.com/google-renders-javascript-xhr-post-requests-317164)";
Search Engine Roundtable, "[GoogleBot supports simple POST requests](https://www.seroundtable.com/google-supports-simple-post-requests-27581.html)";
Google, "[JavaScript SEO basics](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics)";
Search Engine Land, "[Don't use JavaScript to generate a noindex](https://searchengineland.com/google-says-dont-use-a-noindex-tag-in-the-original-page-code-466250)";
Prerender.io, "[Understanding Google noindex rendering](https://prerender.io/blog/understanding-google-noindex-rendering/)";
Google, "[Merchant listing structured data](https://developers.google.com/search/docs/appearance/structured-data/merchant-listing)";
Google Merchant Center, "[VAT-related price issues](https://support.google.com/merchants/answer/10897069)".
