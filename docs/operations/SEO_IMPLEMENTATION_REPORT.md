# SEO implementation report — Djini's Bakehouse

**Date:** 2026-11-25 · **Base:** `35aad41` (the release running in production)
· **Branch:** `ui/auth-restore-admin-orders` · **State: local changes only —
not committed, not pushed, not deployed.**

Nothing outside this repository was changed: no production setting, no Render
variable, no DNS record, no Stripe/PayPal setting, no database row, no Google
account. Every request to the live site was a read-only `GET`/`HEAD`.

---

## 1. Executive summary

**Where the site stood.** The shop is a single-page app, and the server used
to answer *every* address with the same `index.html`. A search engine's first
look at any page — a product, a shelf, the contact page — showed the home
page's title and description, no canonical address, no robots rule and no
structured data; a bake shared in a chat previewed the home page. Worse,
`robots.txt` blocked `/api/`, the API every page draws its content from:
Google renders JavaScript, but it obeys robots.txt for the files a page
fetches, so its renderer would have seen product pages with no product on
them. Unknown addresses answered `200` instead of `404`, `/products/` and
`/products` were two copies of one page, `HEAD` requests were refused, and the
meta description repeated two claims the owner has not yet confirmed
(PENDING.md item 18).

**What changed.**

- The server now writes **each page's own head into the first response**:
  title, description, canonical address, robots rule, share card and
  structured data — built only from data the shop holds. Unknown addresses
  answer **404**, a trailing slash **301**-redirects, `HEAD` answers like `GET`.
- **robots.txt** opens the API (its answers carry `X-Robots-Tag: noindex`, so
  JSON is never listed) and keeps `/admin`, `/account` and `/checkout` closed.
  The **sitemap** gains the four shelf pages and every product photo.
- **Structured data:** `OnlineStore` + `WebSite` on the home page,
  `BreadcrumbList` on every product page. A `Product` with a price
  (`Offer`) is published **only when shelf prices include VAT** — today they do
  not (VAT is added at checkout), and Google treats a German price as the
  whole price. Star ratings appear only with genuine, approved reviews (there
  are none yet). No `LocalBusiness`, no invented facts.
- **Performance:** the three web fonts are preloaded (home-page layout shift
  in the lab: **0.163 → 0.035**), and the first row of shelf photos is fetched
  first.
- **Tests:** 24 new backend tests, 3 new frontend tests, and a 32-point browser
  check of the built site; the earlier rounds' browser suites still pass.

**What this does not do.** It claims no ranking and no indexing: nothing has
been submitted to or verified in Google. The largest remaining levers are
the owner's decisions — VAT-inclusive prices (legal and Google Shopping),
the Impressum, German-language pages, a Google Business Profile, and lighter
product photos (section 10).

---

## 2. Initial SEO audit

Method: read-only requests to the live site on 2026-11-25 (release `35aad41`),
reading the code, and lab measurements (section 7).

| # | Finding | Evidence | Severity |
| --- | --- | --- | --- |
| A1 | **The API was closed to crawlers** | `robots.txt`: `Disallow: /api/`. Home, shelves and product pages load all their content from `/api/*` after the page opens | Critical |
| A2 | **One head for every address** | Served title always `Djini's Bakehouse — Fresh from the oven`; `og:url` always `https://djinisbakehouse.com/`; page titles changed only in the browser, after the app ran | High |
| A3 | No canonical address, no robots rule | 0 `rel="canonical"`, 0 `meta name="robots"` in the served HTML; private pages (desk, account, checkout) indexable in principle | High |
| A4 | Soft 404s | `/no-such-page` → `200` with the shop's shell | Medium |
| A5 | Duplicate addresses | `/products/` → `200`, the same page as `/products` | Medium |
| A6 | No structured data in the served HTML | Product JSON-LD was added by the browser only after the API call that A1 blocked | Medium |
| A7 | Description repeated unconfirmed claims | `small-batch breads, pastries and sweet things baked from scratch every morning` (PENDING.md item 18) | Medium |
| A8 | Thin sitemap | 33 addresses: 7 standing pages + 26 products; no shelves, no images | Low |
| A9 | `HEAD` refused | `405 Method Not Allowed` on every page, `robots.txt` and `sitemap.xml` (uptime monitors and link checkers ask with HEAD) | Low |
| A10 | Two stale crawl files | `frontend/public/robots.txt` and `sitemap.xml` with different rules, never served in production (the server's own routes answer first) | Low |
| A11 | Heavy photos | Product photos are 1200×960 JPEGs of ≈240 KB, shown ≈190 CSS px wide on shelf cards; no `Cache-Control` | Medium (speed) |
| A12 | Layout shifts | Home CLS 0.075–0.177 (lab): fonts arrived after the first render and re-flowed the hero; the announcement bar appears after `/api/store` answers | Medium (speed) |
| A13 | `http://www.` → two redirects | DNS/Render level — out of scope | Low |

**Business facts that shape the SEO** (live public settings and pages): an
online-only home-kitchen bakery with a published contact address in 28359
Bremen; delivery to postcode **28359** only; the site is in **English**;
26 products on 4 shelves (Breads 8, Pastries 10, Desserts 7, Biryani 1);
**0 reviews**; the Impressum is **not published** (PENDING.md item 3); shelf
prices are **net**, with 7 % VAT added at checkout (PENDING.md item 9).

---

## 3. Changes implemented

| File | Change | Why |
| --- | --- | --- |
| `backend/app/seo.py` (new) | Works out each address's head: title, description, canonical, robots rule, share card, JSON-LD, status. Writes it into the built `index.html` between `<!-- seo:start -->` and `<!-- seo:end -->` | A1–A7 |
| `backend/app/__init__.py` | The page catch-all serves the shell with that head and its status (404 for unknown addresses), 301s a trailing slash, answers `HEAD`; falls back to the plain shell if the head ever fails (logged). `X-Robots-Tag: noindex` on every `/api/` answer | A2–A5, A9 |
| `backend/app/routers/content.py` | `robots.txt` without `Disallow: /api/`; sitemap adds shelf pages (`/products?category=…`) and product photos (image sitemap); both answer `HEAD` | A1, A8, A9 |
| `frontend/index.html` | The SEO block between markers, with neutral defaults (no unconfirmed claims); preload of the three font files | A7, A12 |
| `frontend/public/robots.txt`, `sitemap.xml` | Deleted | A10 |
| `frontend/src/lib/titles.js` | Shared wording rules: default description, 160-character clipping at a word, product description rule, shelf titles — mirrored by `seo.py` and kept in step by tests | One wording for server and browser |
| `frontend/src/App.jsx` | After the first in-app move, removes the landing page's server-written canonical/robots/JSON-LD (they describe that one address) | No stale canonical after navigating |
| `frontend/src/pages/Products.jsx` | Tab title names the shelf; first two cards' photos fetched first | Matching titles; LCP |
| `frontend/src/pages/ProductDetail.jsx` | Same description rule as the server; client-side JSON-LD removed (the server writes it) | One source of structured data |
| `frontend/src/pages/NotFound.jsx` | Adds its own `noindex` only when none is there — a wrong address loaded directly already has the server's; one reached inside the app gets the page's | Exactly one robots tag |
| `frontend/src/components/ProductCard.jsx`, `PhotoTile.jsx` | `eager` passes through to the photo; eager photos get `fetchpriority="high"` | LCP |
| `backend/tests/test_seo.py` (new), `backend/tests/test_api.py`, `frontend/src/lib/titles.test.js` | Tests (section 8); the old robots test expected `Disallow: /api/` | — |

**How a crawler now sees each kind of page**

| Address | Status | Robots | Canonical | In sitemap |
| --- | --- | --- | --- | --- |
| `/` | 200 | index (`max-image-preview:large`) | itself | yes |
| `/products` | 200 | index | itself (`?page=N` kept for N>1; sort/filter queries dropped) | yes |
| `/products?category=<shelf>` | 200 | index | itself | yes, each shelf |
| `/products?search=…` | 200 | `noindex, follow` | the shelf or `/products` | no |
| `/products/<bake>` | 200 | index | itself (queries such as `utm_*` dropped) | yes, with photo |
| `/products/<unknown or hidden bake>` | **404** | `noindex` | — | no |
| `/bulk`, `/contact`, `/privacy`, `/terms`, `/refunds` | 200 | index | itself | yes |
| `/imprint`, `/impressum` | 200 | index **once the notice is written**, `noindex, follow` until then | `/imprint` | once written |
| `/cart`, `/login`, `/register`, `/forgot-password` | 200 | `noindex, follow` | — | no |
| `/checkout`, `/account/*`, `/admin/*`, `/reset-password`, `/unsubscribe`, `/auth/callback` | 200 | `noindex, nofollow` — robots.txt also closes `/checkout`, `/account` and `/admin`, so Google does not fetch those at all; the tag is for anything that does | — | no |
| anything else | **404** | `noindex` | — | no |
| `…/` (trailing slash) | **301** to the address without it, query kept | — | — | — |

---

## 4. Technical SEO status

| Area | Status |
| --- | --- |
| Titles | Unique per page, from the same table the browser uses (`titles.js` ⇄ `seo.py`, tested). Products: `<name> — Djini's Bakehouse`; shelves: `<shelf> — Djini's Bakehouse` |
| Descriptions | Unique per page, ≤160 characters, cut at a word. Built from shop data only — shelf names, product names, the town in the published address, the contact details actually published. No claim from PENDING item 18 |
| Language | `<html lang="en">`, `WebSite.inLanguage: "en"` — true to the content. No `hreflang` (one language) |
| Canonicals | On every indexable page, absolute, built from `PUBLIC_BASE_URL` when set (otherwise the request's address) |
| Robots | Per page as in section 3; `/api/` answers carry `X-Robots-Tag: noindex` |
| robots.txt | API open; `/admin`, `/account`, `/checkout` closed; sitemap listed |
| Sitemap | On today's live data 37 addresses (was 33): 7 standing pages, 4 shelves, 26 bakes each with its photo — plus the Impressum once written. In the tests and on the scratch shop every address answers 200 and names itself canonical |
| Status codes | 404 for unknown addresses and hidden/unknown bakes; 301 for trailing slashes; `HEAD` = `GET`. The person still sees the shop's own not-found page |
| Rendering | The head is in the first response; the page body is still drawn by JavaScript. With the API open, Google can render it. Full server-side rendering of the body is not needed for this catalogue and was not attempted |
| Social cards | `og:*` and `twitter:*` per page; products use their own photo, other pages the shop's photo |
| Headings | One `<h1>` per public page (checked on 8 pages in the browser) |
| Internal links | Shelves are real links from the home page's shelf tiles, the footer and every product's breadcrumbs. The shop page's own filters and its pager are buttons — harmless today, because every shelf fits on one page (at most 10 bakes, 12 per page) and every bake is in the sitemap. If a shelf outgrows one page, make the pager links |
| Images | Product photos carry the product's name as `alt`; the shop's first photo and the first shelf photos are fetched first |
| Private pages | Never in the sitemap; `noindex, nofollow`; desk API answers `Cache-Control: no-store` (unchanged) |

---

## 5. Product SEO status

**Product pages** have their own title, description (the product's own text,
plus its shelf when that fits in 160 characters), canonical address, share
card with the product photo, and a `BreadcrumbList` (Shop → shelf → product).

**Price markup is deliberately held back.** Google reads a price in Germany as
the whole price, VAT included, and does not document the
`valueAddedTaxIncluded` flag. The shop's shelf prices are net — 7 % is added
at checkout — so an `Offer` would show searchers €3.75 for a croissant that
costs €4.01 plus delivery. Publishing that would be misleading structured
data. The code therefore:

- publishes `Product` + `Offer` (price, currency, availability, condition,
  seller, `valueAddedTaxIncluded: true`) **as soon as `TAX_MODE=inclusive`** —
  no further code change needed;
- publishes `Product` with `AggregateRating` (no offer) when a bake has
  genuine approved reviews — the numbers are exactly the page's own;
- publishes no `Product` at all otherwise, because a product result needs an
  offer or reviews, and an incomplete one is only flagged as an error.

**Catalogue data** (live, 26 products): all have a description (72–163
characters; one longer than 160 is clipped at a word), a shelf, a photo,
ingredients and allergens (shown on the page as "Contains: …" — their
accuracy is the owner's, not checked here). 0 reviews.

**Google Merchant Center — not ready**, for reasons only the owner can clear:

1. **Prices must include VAT** for German shoppers, in the feed and on the
   page ([Google: VAT-related price issues](https://support.google.com/merchants/answer/10897069)).
   The shop shows net prices — the same question as PENDING.md item 9, and
   also a consumer-pricing question under German law (PAngV) for the owner's
   adviser.
2. **The Impressum** (PENDING.md item 3) — Merchant Center expects clear
   business identity and contact details, and German law expects an Impressum.
3. **Delivery to one postcode** has to be expressible in Merchant Center's
   shipping settings; check that before relying on Shopping listings. For a
   one-postcode delivery area, a Google Business Profile (section 9) is likely
   the better first step.
4. Own-brand bakes have no GTIN: the feed would use brand = "Djini's
   Bakehouse", the product id as MPN, and `identifier_exists = no`.

---

## 6. Keyword strategy (preliminary)

**No search volumes are quoted, because none were measured.** No keyword tool
(Google Ads Keyword Planner, Search Console data) was available. What follows
comes from the catalogue, the delivery area, and spot checks of search results
made with a US-based web search tool — which is **not** a German Google
results page. Treat every priority below as a hypothesis to confirm with
Search Console's Performance report after 4–8 weeks of indexing, and with
Keyword Planner.

**Market facts.** Delivery covers postcode 28359 (Horn-Lehe, with the
university and the Technologiepark). The site is in English, which suits an
international campus audience but is weak for German queries. Spot checks:

- the shop's own name returned no result for the site (Search Console will
  show what Google has indexed);
- "order/delivery" queries for Bremen are dominated by delivery platforms
  (Wolt, Lieferando, Uber Eats);
- "Bäckerei Bremen / 28359" queries are dominated by directories (Das Örtliche,
  golocal, cylex) and established bakeries in the postcode;
- for the shop's distinctive bakes — maritozzo, croffle, Bengali/kacchi
  biryani, wool roll bread — the checks found **no dedicated Bremen seller**.
  That means little competition, but demand is unmeasured.

| Cluster | Example queries (EN / DE) | Intent | Target page | Fit today |
| --- | --- | --- | --- | --- |
| Brand | Djini's Bakehouse, Djinis Bakehouse Bremen | navigational | `/` | Good once indexed |
| Distinctive bakes | maritozzo Bremen, croffle Bremen, Bengali biryani / kacchi biryani Bremen, wool roll bread, beef curry puff | transactional, low competition | each product page (`/products/maritozzo`, `/products/croffle`, `/products/bengali-biryani`, …) | Good — own title, description, photo; better with richer descriptions |
| Shelves | pastries / desserts / breads delivery Bremen; Gebäck, Kuchen, Brot bestellen Bremen | commercial | `/products?category=…` | Fair — EN only. A short written intro per shelf would help: the shop already stores a description per shelf and uses it as the shelf's meta description when one is set, but none is, and the desk cannot edit it yet (a small addition for a later round) |
| Local bakery | bakery near University of Bremen; Bäckerei Horn-Lehe; Bäckerei 28359 | local | `/`, `/contact` | Weak on the website alone — this is Google Business Profile territory |
| Events & catering | bulk pastries Bremen, Gebäck für Veranstaltung Bremen, Catering Uni Bremen | commercial | `/bulk` | Fair — EN only |
| Seasonal | Christstollen Bremen, Christmas Stollen | seasonal | `/products/christmas-stollen` | Page exists (sold out for now; it stays indexed and says so) |

**What would move it most** (owner decisions, section 10): German-language
pages with `hreflang` (the largest lever for local German queries, and a real
project), a Google Business Profile, genuine reviews, and descriptions that
say what only the baker knows — never generic filler, never claims the owner
cannot stand behind.

---

## 7. Performance

All figures are **lab** measurements — Chromium emulating a 412×915 phone,
simulated slow 4G (1.6 Mbps, 150 ms round trip), 4× CPU slowdown, cold
cache, median of three loads. They are **not field data**. Google's page
experience signals use real-user data (Chrome UX Report). A shop with this
traffic is likely to have none, and Search Console will then say "not enough
data".

**Live site, before any change** (`35aad41`):

| Page | TTFB | FCP | LCP | CLS | Transfer |
| --- | --- | --- | --- | --- | --- |
| `/` | 213 ms | 1984 ms | 4288 ms | 0.177 | 929 KB / 19 requests |
| `/products` | 196 ms | 1884 ms | 6044 ms | 0.053 | 1053 KB / 19 |
| `/products/butter-croissant` | 205 ms | 1816 ms | 4036 ms | 0.053 | 566 KB / 13 |
| `/products?category=breads` | 196 ms | 1888 ms | 5884 ms | 0.085 | 1023 KB / 19 |

**Where the layout shift came from** (live home page, layout-shift attribution):

- **≈0.05 on every page:** the announcement bar appears ≈80 ms after
  `/api/store` answers and pushes the page down 48 px.
- **0.02–0.12 on the home page:** the three web fonts were requested only
  once the app had drawn text (≈1.7 s), arrived at 2.9–3.3 s, and re-flowed
  the hero (its heading lost lines; the block re-centred).

**Changes and their measured effect** (scratch copy of the shop, same lab
profile; demo data, so absolute numbers differ from the live site):

| Page | FCP before → after | LCP before → after | CLS before → after |
| --- | --- | --- | --- |
| `/` | 1884 → 2336 ms | 3576 → 3124 ms | **0.163 → 0.035** |
| `/products` | 1788 → 2280 ms | 1788 → 2280 ms (heading) | 0.040 → 0.035 |
| `/products/butter-croissant` | 1752 → 2256 ms | 3348 → 3568 ms | 0.035 → 0.035 |

- **Font preload:** the font re-flow is gone, and the 0.035 left is the
  announcement bar. The trade-off: on this deliberately slow profile, first
  paint comes ≈0.45–0.5 s later, because the fonts now download alongside the
  app instead of after it. LCP is roughly unchanged — the same bytes in a
  different order. On ordinary 4G or Wi-Fi the delay is small. The change is
  three lines in `index.html`, easy to revert if the owner prefers the
  earlier first paint.
- **First-row shelf photos first** (A/B with the font preload in both):
  pastries LCP 5036 → 4788 ms, breads 5324 → 5192 ms. Small but consistent.

**Not changed — recommendations:**

1. **Lighter photos (largest win).** Re-export product photos at most ≈1000 px
   wide, WebP or JPEG quality ≈75–80, *before uploading* — no code needed, and
   likely to save around a second of LCP on slow connections. A later code
   step: make card-sized copies at upload and serve them with `srcset`. That
   changes the desk's upload path, so it is left for an approved round.
2. **Cache headers for uploaded photos.** Upload names are random and never
   reused, so `Cache-Control: public, max-age=31536000, immutable` would be
   technically safe. But a photo deleted later (for example, one uploaded by
   mistake) could then survive in browser and CDN caches for up to a year.
   A shorter `max-age=86400` is the cautious middle. Owner's choice.
3. **The announcement bar's shift (≈0.035–0.05 on every page).** Fixing it
   means giving the app the shop's public settings before its first draw
   (embedded in the page, or remembered from the last visit). Those settings
   also feed checkout, so the change was not made here.
4. **Code splitting.** The main script is 549 kB (169 kB gzipped): react-dom
   38 %, axios 11 %, react-router 7 %, and the customer pages (checkout,
   account, order detail) ≈140 kB. The desk is already split off. Lazy-loading
   checkout and account pages would trim the first load; it touches the
   checkout route's loading, so it needs its own reviewed round.

---

## 8. Testing

All run on this machine on 2026-11-25, against the final code. The commands
are in section 13.

| Check | Result |
| --- | --- |
| Backend suite (`pytest -q`) | **619 passed, 0 failed** (595 before this round: 24 new in `tests/test_seo.py`; the robots test in `test_api.py` updated). The 2 warnings were there before |
| — `tests/test_seo.py` | 24/24: each page's head; OnlineStore/WebSite; address read only when it is one; product head; price markup in both tax modes; stars only from approved reviews (a hidden review counts in neither); a bake with no shelf or photo; shelves, filters, pages, search; standing pages; contact description from what is published; 404s; **every route in `App.jsx` answers 200**; `HEAD`; a hidden bake is 404 and leaves the sitemap; 301s; private and transactional robots; Impressum indexed once written; a hostile description cannot break out of the page; head failure falls back to the plain shell; CSP and `no-cache` kept; robots.txt; `X-Robots-Tag`; sitemap (unique, absolute, shelves, photos, every address 200 and self-canonical); titles and description rules identical to `titles.js` |
| Frontend unit tests (`npm test`) | 63/63 (3 new, mirroring the backend's wording cases) |
| Frontend build (`npm run build`) | OK, reproducible — `index-D4KrLP6i.js` 549 kB (169 kB gzipped). The "chunk larger than 500 kB" warning was there before. The three font preloads resolve to the stylesheet's own hashed files, so nothing downloads twice |
| Scope check (names used but never defined, unused imports) | 95 files, 0 problems |
| Browser — SEO checks on the built site (scratch copy of the shop behind the real server) | **32/32**: unique titles/descriptions ≤160; `og:url` = canonical; 404, 301 and `HEAD` through the real server; no price markup in the shipped tax mode; share images load; robots.txt and `X-Robots-Tag`; all 39 sitemap addresses 200 and self-canonical; after the app runs: the same title and description, one JSON-LD block, the canonical kept, the main photo first; server tags survive the first render on 5 landing pages and go at the first in-app move; one `<h1>` on 8 pages; the not-found page carries exactly one `noindex`, also after a wrong turn inside the app; no console errors (the one logged line is the 404 page's own status) |
| Browser — the earlier rounds' suites | smoke 78 page loads, no problems · design 85/85 · launch round 83/83 · small screens 65/65 · admin orders 40/40 · payment journey 40/40 (stand-in payment pages, no real payment) · payment states 8/8 · sign-in journey 27/27 · sign-in fixes 28/28 · restored sign-in pages 64/64 · text sign-in 29/29 · phone round 92/93 — its one failure ("Due next" labels) depends on the dates of scratch orders and is identical in the previous release's baseline |
| Not counted | `auth_pages_round`: it checks the sign-in design that was rejected and rolled back on 2026-11-24, so 28 of its checks fail by design. Obsolete since the previous round |

**Found by testing and fixed in this round:**

- The design round caught a **duplicate `noindex`** on the not-found page
  (the server's tag plus the page's own). The page now adds its own only
  when none is present.
- The first full suite run failed one SEO test: it assumed the first listed
  product had a shelf and a photo, and other suites add bare ones. The test
  now picks a complete product, and a new test covers a bare one.

**Harness incidents (not product faults):**

- A Git Bash setting stopped the scratch shop's wrapper from killing its
  server, so one lab measurement ran against a stale build. It was found,
  the server stopped, and the measurement repeated.
- Two suites first ran without their database setting and were re-run
  green.
- A password-reset rate limit (429) hit when 13 suites shared one server;
  re-run first in a fresh one, it passed 28/28.

**Not verified:** anything in Google (indexing, Search Console reports, the
Rich Results Test), a real phone, real payments, and the live site with
this release (it is not deployed).

---

## 9. Google Search Console setup (owner)

Nothing here has been done — it needs the owner's Google account and DNS.

1. **Add the property.** [search.google.com/search-console](https://search.google.com/search-console)
   → *Add property* → **Domain** → `djinisbakehouse.com`. Google shows a `TXT`
   record; add it where the domain's DNS is managed, then *Verify*. A Domain
   property covers `https://`, `http://` and `www.` at once, and needs no
   code change. (A URL-prefix property with an HTML tag would need a code
   change — not needed.)
2. **After the SEO release is deployed**, *Sitemaps* → submit
   `https://djinisbakehouse.com/sitemap.xml`. Expect "Success" and 30-plus
   discovered addresses.
3. **URL Inspection** → *Test live URL* for `/`, `/products`, one shelf and one
   product page → *View tested page*: the screenshot should show the bakes, and
   the HTML should contain the product's name. Then *Request indexing* for the
   home page and the main shelves (the daily quota is small).
4. **Settings → robots.txt**: the fetched file should no longer list `/api/`.
5. **Expected reports:** *Page indexing* will list the basket, log-in and
   private pages as "Excluded by 'noindex'" and retired bakes as "Not found
   (404)" — both correct. *Breadcrumbs* should appear under Enhancements.
   *Product snippets* appear only once there are reviews or VAT-inclusive
   prices. *Core Web Vitals* will most likely say "not enough data".
6. Optional: **Bing Webmaster Tools** can import the property from Search
   Console in a few clicks.

Indexing takes days to weeks, and nothing guarantees it.

**Google Business Profile.** Google's guidelines allow a business without a
staffed storefront that serves customers at their address — delivery — to
have a **service-area** profile, with the home address hidden
([guidelines](https://support.google.com/business/answer/3038177),
[service areas](https://support.google.com/business/answer/9157481)).
Whether the shop fits — delivery only, or also pick-up at the address — is the
owner's call. The profile name must be the real business name. Verification
for home businesses is typically a short video. Only after that decision
should `LocalBusiness` markup be considered; today's `OnlineStore` markup
carries only what the site already publishes.

---

## 10. Outstanding issues

**Owner actions**

| # | Action | Why | Where |
| --- | --- | --- | --- |
| O1 | Decide on VAT-inclusive shelf prices (with a tax/legal adviser) | German consumer prices are normally shown including VAT (PAngV); Google Shopping requires it; switches on price markup automatically | PENDING.md item 9 |
| O2 | Publish the Impressum | Legal expectation in Germany; trust; Merchant Center; the page then becomes indexable and joins the sitemap automatically | PENDING.md item 3 |
| O3 | Verify the domain in Search Console, then submit the sitemap after deploy | Section 9 | PENDING.md item 19 |
| O4 | Decide on a Google Business Profile (service area 28359?) | Local searches; reviews | Section 9 |
| O5 | Lighter product photos | LCP, data use | Section 7 |
| O6 | Confirm or reword the claims of PENDING.md item 18 | They are kept out of search snippets until confirmed | PENDING.md item 18 |
| O7 | Confirm `PUBLIC_BASE_URL=https://djinisbakehouse.com` in Render | Pins every canonical address to the main domain whatever host a request arrives on | Render → Environment |
| O8 | Ask happy customers for genuine reviews (never incentivised fakes) | Stars in results need real reviews; the review system and moderation exist | — |

**Engineering follow-ups (each needs its own approval)**

- Card-sized photo copies + `srcset` (upload path).
- A field in the desk for each shelf's short description (stored already,
  used as the shelf's meta description, but not editable today).
- Store settings available before the first draw (announcement-bar shift).
- Lazy-loading checkout and account pages.
- German-language pages and `hreflang`, if the owner wants German search.
- Path-style shelf addresses (`/shop/breads`) instead of `?category=` —
  optional; Google handles the current form, and the current form is what is
  canonical and in the sitemap.
- A known edge: a `?page=N` past the last page names itself canonical in the
  first answer, while the app quietly shows the last real page. No link on
  the site leads there; fixing it would tie the server to the shop page's
  page size (12).

---

## 11. Risk assessment

| Change | Risk | Mitigation |
| --- | --- | --- |
| Per-page head written by the server | A database error while building the head | The plain shell is served instead (logged, tested). Each page view adds a few small queries |
| 404 for addresses the server does not know | A page added to `App.jsx` but not to `seo.py` would answer 404 + `noindex` | A test reads every route in `App.jsx` and fails if one does not answer 200 |
| 301 for trailing slashes | Any link written with a trailing slash takes one redirect | Only addresses ending in `/`; query kept; tested |
| robots.txt opens `/api/` | API responses fetched by crawlers | `X-Robots-Tag: noindex` on every API answer; private API routes still need log-in |
| Price markup held back | No price in search results until VAT-inclusive pricing | Intentional (section 5); switches on with `TAX_MODE=inclusive` |
| Font preload | ≈0.5 s later first paint on very slow connections | Measured; three lines to revert |
| First-row photos first | A little bandwidth taken from lower cards | Two cards only |
| `HEAD` on pages | None known; the API keeps answering `HEAD` with 405 as before | Tested |
| Static crawl files deleted | The Vite dev server no longer serves `/robots.txt` | Dev only; production never served them |
| Server-written tags removed after the first in-app move | — | Search engines do not navigate in-app; tested in the browser |

Not touched, as the brief requires: payments, checkout, refunds, shipping,
VAT, orders, sign-in and sign-up, SMS, Facebook Login, permissions, the desk,
customer data, the schema, environment variables, DNS.

---

## 12. Roadmap

**Next 30 days**
- Review, then deploy this release (section 13); verify live.
- Search Console: domain property, sitemap, URL inspection (section 9).
- Owner: Impressum (O2), photo re-exports (O5), PUBLIC_BASE_URL check (O7),
  Business Profile decision (O4).

**30–60 days**
- Read Search Console: which queries already bring impressions, which pages are
  indexed, any coverage errors. Re-prioritise section 6 from that data.
- VAT-inclusive prices if the adviser agrees (O1) — price markup follows.
- Richer product descriptions written by the baker; shelf intros.
- Card-sized photos + `srcset`.

**60–90 days**
- German pages (`hreflang`) if the data shows German demand.
- Genuine reviews flowing; check that *Product snippets* appear.
- Re-measure (PageSpeed Insights lab; field data if Chrome UX Report has
  enough visits). Consider the announcement-bar fix and code splitting.

---

## 13. Deployment checklist

1. Review the diff (`git diff`, plus the new files `backend/app/seo.py`,
   `backend/tests/test_seo.py` and this report).
2. Run the checks: `cd backend && venv/Scripts/python.exe -m pytest -q`,
   `cd frontend && npm test && npm run build`.
3. Commit and push to `main` — **this deploys** (Render). Owner's approval
   first. No migration, no new environment variable, no new dependency.
4. After the deploy, read-only checks on `https://djinisbakehouse.com`:
   - `/products/butter-croissant`: title `Butter Croissant — Djini's Bakehouse`;
     canonical is itself; exactly one JSON-LD block (BreadcrumbList).
   - `/no-such-page` → 404; `/products/` → 301 to `/products`;
     `curl -I https://djinisbakehouse.com/` → 200.
   - `/robots.txt` has no `Disallow: /api/`; `/sitemap.xml` lists the shelves
     and photos; any `/api/` answer carries `X-Robots-Tag: noindex`.
   - Google's [Rich Results Test](https://search.google.com/test/rich-results)
     on the home page (no errors) and a product page (Breadcrumb valid).
   - A link to a product pasted into a chat previews that product.
5. Then Search Console (section 9).
6. **Rollback:** revert the commit and push. Nothing in the database depends
   on this release.

---

## References

- Google Search Central: [JavaScript SEO basics](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics),
  [robots.txt](https://developers.google.com/search/docs/crawling-indexing/robots/intro),
  [sitemaps](https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview),
  [Product structured data](https://developers.google.com/search/docs/appearance/structured-data/product),
  [Breadcrumb structured data](https://developers.google.com/search/docs/appearance/structured-data/breadcrumb)
- Google Merchant Center: [VAT-related price issues](https://support.google.com/merchants/answer/10897069),
  [checkout requirements](https://support.google.com/merchants/answer/9158778)
- Google Business Profile: [guidelines](https://support.google.com/business/answer/3038177),
  [service areas](https://support.google.com/business/answer/9157481)
- RFC 9110 §9.1 — servers support `GET` and `HEAD`.
