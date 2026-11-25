# What is still pending — and how to finish each item

Written 2026-11-23, with the release that added **phone-number sign-in** and
the **new dashboard**. Checked against the live shop's public settings
(`https://djinisbakehouse.com/api/store`) and the code in this repository.

Updated 2026-11-24, with the release that restores the sign-in pages, brings
back the logo's own burgundy and shortens the shop's wording: items **17**
and **18** are new. Since the first writing, the live shop also reports text
sign-in (item 11) and "Continue with Facebook" (item 12) as switched on.

Updated 2026-11-25, with the release that puts **Email | Phone tabs** on the
log-in and sign-up pages and rebuilds the desk's **Users** page: items 10,
11 and 14 now describe the tabs. Nothing new to do.

Updated again 2026-11-25, with the **search-engine round** (ready for
review, not deployed yet — `docs/operations/SEO_IMPLEMENTATION_REPORT.md`):
item **19** is new. Checked against the live shop the same day, items **5**
and **6** read as done: the address has its postal code and town, the
opening hours no longer contradict each other, the notes are current, and
the delivery area is 28359.

Everything below is yours to do or to decide — none of it can be done from
the code. Each item says **what is open**, **why it matters**, **how to fix
it**, and **how to see that it worked**. No step asks for a real payment as
a test, and no secret belongs in this repository: keys go into Render only.

> Where "Render → Environment" is written, it means: [dashboard.render.com](https://dashboard.render.com)
> → the service **djinis-bakehouse** → **Environment** → add or edit the
> value → **Save changes**. Render restarts the shop by itself (2–3 minutes).

---

## At a glance

| # | What | How urgent | Time |
| --- | --- | --- | --- |
| 1 | Card payments are **live** but were never rehearsed | **Before the next card order** | 30–60 min |
| 2 | Stripe webhook address and events | **Before the next card order** | 10 min |
| 3 | Legal notice (Impressum) is not published | **Soon** | 15 min |
| 4 | Is the shop's email switched on? | **Soon** | 15 min |
| 5 | Address typo, opening hours, old notices | Soon | 5 min |
| 6 | Delivery area is empty | Your decision | 5 min |
| 7 | VAT number in Stripe | Soon | 5 min |
| 8 | The FRESHBAKE15 minimum needs re-saving | When you use the code | 2 min |
| 9 | VAT classes for every product (only for VAT-inclusive prices) | Your decision | 1–2 h |
| 10 | Phone sign-in: what to know, what to try | Today, once | 10 min |
| 11 | Texted sign-in codes through Bird Verify — built, switched off | Optional | 20 min + cost |
| 12 | "Continue with Facebook" — built, switched off | Optional | 30 min |
| 13 | PayPal — built, switched off | Optional | 45 min |
| 14 | A walk through the site on a real phone | Soon | 15 min |
| 15 | Smaller decisions (visitor address header, server region, CSV column) | No hurry | — |
| 16 | Housekeeping (backups, public repository, your PC's memory) | No hurry | — |
| 17 | Brand colour: back to the mark's burgundy | **Right after this release** | 1 min |
| 18 | Words on the site that only you can confirm | No hurry | 10 min |
| 19 | Search engines: Search Console, then the sitemap | Step 1 now; step 2 after the search release | 20 min |

---

## 1. Card payments are live, but were never rehearsed

**What is open.** The live shop reports `card_payments: true` and
`card_test_mode: false` — the Stripe key in Render is a **live** key, so a
card payment at checkout charges real money. The payment code is covered by
the automated tests (with Stripe replaced by a stand-in), but it has **never
been run against a real Stripe account from this side** — no Stripe
credentials exist on the build machine. If you have already bought something
yourself and the order turned "Paid", this item is done.

**Why it matters.** If a key, the webhook or a payment method is set up
wrongly, the first customer finds out: they pay, and the order never turns
"Paid".

**How to fix it — choose one.**

*Option A — the read-only check (no risk, 10 minutes).*

1. Render → the service → **Shell**.
2. Run `python backend/scripts/check_payments.py`
3. It only reads: it asks Stripe about the account, the webhook and the
   payment methods, and prints no secret. It ends with
   **"Nothing blocks online payments"** or a list of what does. Fix what it
   lists (items 2–4 below are the usual ones), then run it again.
4. Watch the first real card order: its page must say **Paid** within a few
   seconds, and the desk's *Orders → Payment check* tab must show nothing
   that needs a look.

*Option B — a full rehearsal with test cards (most certain, 45 minutes).*

Do this at a quiet hour. While it runs, the checkout says "test mode" and
takes no real money — **a real customer ordering by card in those minutes
would not be charged**, so keep it short.

1. In Stripe, switch the dashboard to **Test mode** and copy the test secret
   key (`sk_test_…`).
2. Still in test mode: Developers → Webhooks → add an endpoint for
   `https://djinisbakehouse.com/api/payments/webhook` with the events in
   item 2, and copy its signing secret (`whsec_…`).
3. Render → Environment: put those two values into `STRIPE_SECRET_KEY` and
   `STRIPE_WEBHOOK_SECRET`. **Write the live values down first** — you put
   them back in step 6.
4. In the Render Shell: `python backend/scripts/check_payments.py --rehearse`
5. On the site, buy something with the test card `4242 4242 4242 4242` (any
   future date, any CVC). The order must turn **Paid**. Then refund it from
   the order's sheet at the desk (*Orders → Manage*). The full list of things
   to try is `docs/operations/PAYMENTS_GO_LIVE.md`, step 2.
6. Put the live key and the live signing secret back, and run Option A.

**How to see it worked.** `check_payments.py` ends with "Nothing blocks
online payments", and a paid order shows **Paid** on its page.

---

## 2. Stripe webhook: the address and the events

**What is open.** I cannot see your Stripe dashboard. The webhook was first
set up for the old `…onrender.com` address, and two events were added to the
shop later.

**Why it matters.** The webhook is how Stripe tells the shop "this order is
paid", "this was refunded", "this was disputed". A webhook on the old
address delivers nowhere.

**How to fix it.**

1. Stripe dashboard (**live** mode) → Developers → Webhooks.
2. There must be exactly **one** endpoint, with the URL
   `https://djinisbakehouse.com/api/payments/webhook`. Edit or delete any
   endpoint that still points to an `onrender.com` address.
3. The endpoint must listen to all of these events:

   ```
   checkout.session.completed
   checkout.session.async_payment_succeeded
   checkout.session.async_payment_failed
   checkout.session.expired
   charge.refunded
   refund.updated
   charge.dispute.created
   charge.dispute.closed
   ```

4. If you created a new endpoint, copy its signing secret (`whsec_…`) into
   `STRIPE_WEBHOOK_SECRET` (Render → Environment).

**How to see it worked.** In Stripe, the endpoint's page shows recent
deliveries answered with **200**. `check_payments.py` (item 1) checks the
address and the event list for you.

---

## 3. The legal notice (Impressum) is not published

**What is open.** The live shop reports `legal_notice_published: false` —
the footer has no "Legal notice" link.

**Why it matters.** A shop that sells to customers in Germany is expected to
name who runs it. What exactly must be written there is a legal question —
the app does not write it for you, and neither do I.

**How to fix it.** Log in → *Baker's desk → Settings → Legal notice
(Impressum)* → write your details (name, address, contact, and whatever your
registration requires) → save. If you are unsure what belongs there, ask your tax adviser
or use a generator from a source you trust.

**How to see it worked.** The footer shows a **Legal notice** link on every
page.

---

## 4. Is the shop's email switched on?

**What is open.** I cannot see this from outside. Without a mail account,
the shop sends **nothing**: no receipts, no order updates, no "forgot
password" links.

**How to check.** Render → the service → **Logs** → look at the lines
written at start-up. If one says **"Email is OFF"**, it is not set up.
Or: use *Forgot password* with your own address — a mail should arrive
within a minute.

**How to fix it.** Render → Environment → set `SMTP_HOST`, `SMTP_USER`,
`SMTP_PASSWORD` and `SMTP_FROM` (and `SMTP_PORT` only if it is not 587) from
your mail provider, and `PUBLIC_BASE_URL=https://djinisbakehouse.com` so
every link in an email carries your real address.

**How to see it worked.** The start-up log no longer says "Email is OFF",
and the *Forgot password* mail arrives with a link that starts with
`https://djinisbakehouse.com/`.

---

## 5. Shop details that read wrong on the live site

All of these are in *Baker's desk → Settings*; each takes a minute.

| Where | What the site shows today | What to do |
| --- | --- | --- |
| Contact address | `Mary Astell Streße, 4` | "Straße" is misspelled; add the postal code and the town |
| Opening hours | `Monday - Saturday: 7 am–5 pm` and then `Saturday - Sunday: Closed` | Saturday is in both lines — decide which is true |
| "Today" note | `Kacchi is out on 7th November \| PRE ORDER NOW` | 7 November has passed — update or clear it |
| Announcement bar | `… \| MSI takes 20% off in order` | Reads unfinished — reword it, and check that a code called `MSI` exists under *Promotions* |

**How to see it worked.** Reload the home page: the footer and the bars show
the new text.

---

## 6. The delivery area is empty

**What is open.** No postal codes are set, so checkout accepts **any**
address — an order from another city or another country goes through.

**How to fix it.** *Settings → Delivery area* → the postal codes you deliver
to, separated by commas or one per line → save. Leave it empty only if you really do send
everywhere.

**How to see it worked.** Checkout shows "We deliver to postal codes …", and
an address outside the list is refused before anything is paid.

---

## 7. Your VAT number in Stripe

**What is open.** Stripe's own receipts and invoices show the business
details from your Stripe account, not from the shop.

**How to fix it.** Stripe dashboard → Settings → Business → Tax details →
enter the VAT / tax number your registration gave you. (I do not know your
number, and the shop never stores it.)

---

## 8. The FRESHBAKE15 minimum needs re-saving

**What is open (as of the last check, 2026-11-06).** The code FRESHBAKE15
carries a minimum basket of 40 that was typed when the shop still worked in
dollars. Until you confirm it as a euro amount, **customers cannot use the
code**.

**How to fix it.** *Baker's desk → Promotions* → FRESHBAKE15 shows
"minimum pending review" → open it → check that €40 is what you want → save.

**How to see it worked.** The row says "from €40.00" instead of "pending
review".

---

## 9. VAT classes for every product (only if you want VAT-inclusive prices)

**What is open.** The shop runs in the "exclusive" mode: shelf prices are
net and 7 % is added at the till. The German consumer mode — prices on the
shelf already include VAT — is built and tested, but stays off until
**every** product has a VAT class that you approved. At the last check all
25 products were unclassified.

**Why it is yours.** Which rate applies to which bake is a tax decision. The
app never chooses it for you.

**How to fix it.**

1. *Baker's desk → Products* → the **Needs VAT review** button lists every
   product still open → give each one its class (each change is logged).
2. When that list is empty, tell me: the shop has an admin-only report
   (`/api/admin/vat/report`) that names anything still blocking the switch,
   and I will read it with you before step 3.
3. Render → Environment → `TAX_MODE=inclusive`.

Details: README, "Turning on VAT-inclusive pricing". Until then nothing is
broken — the shop simply keeps adding 7 % at the till.

---

## 10. Phone sign-in — what to know, and what to try once

**What is new.** The sign-up and log-in pages have two tabs, **Email** and
**Phone**, and a password. A customer can open an account with just a name,
a phone number and a password. The pages open on the tab that browser last
signed in with.

**Try it once on the live site (10 minutes).**

1. Open `/register` in a private window, choose the **Phone** tab, and sign
   up with a name, **your own mobile number** (`0176…` or `+49 176…`) and a
   password.
2. Log out, then on *Log in* → **Phone**, log in with the same number written
   differently (with spaces, or with `+49`). It must be the same account.
3. Open *Profile*: there is an "Email (optional)" field — add an address
   and save. From then on the account also gets receipts by email.
4. As admin: *Baker's desk → Users* → find the account by its number → tap
   its row to open its card → *Account* → **Make a reset link**. That is how
   a customer without an email gets a new password: you send them the link
   (WhatsApp, SMS, or read it out). It works once, for 30 minutes.

**Things to know.**

- **A phone-only account has no email.** It gets no receipt and no order
  updates by mail (its orders are on its order page), and "Forgot password"
  cannot mail it a link — the customer contacts you, and you make the link
  (step 4).
- **A number is trusted for text sign-in only once its owner confirms it.**
  With text sign-in on (item 11), a customer confirms their number once from
  their profile with a texted code. Only a confirmed number signs in by text,
  a changed number has to be confirmed again, and admins never sign in by
  text. Signing up with a number and a password needs no confirmation.
- **One number, one account.** If a customer says "it tells me my number is
  already used" and it is not their account, tell me — there is no desk
  button yet to free a number.
- A number typed without a country (`0176…`) is read as German (+49).
  A customer from elsewhere types `+` and their country code. To change the
  default: Render → Environment → `PHONE_DEFAULT_COUNTRY_CODE` (digits only).
- Marketing emails cannot be sent to a phone-only account, and the desk does
  not offer to record consent for one.

---

## 11. Texted sign-in codes through Bird Verify — built, switched off

**What it adds.** A "Text me a sign-in code instead" button under the log-in
page's **Phone** tab, and a
"Text sign-in" panel on each customer's profile. A customer confirms their
number once with a texted code; after that they can sign in with six digits
by SMS instead of their password. Bird generates, sends and checks every
code — the shop never sees one. Admins never sign in by text.

**Why it is off.** Texts cost money per message, and the shop's code needs
the Bird key, which is not set yet. The button appears by itself once the
key exists — never before.

**How to switch it on** (your Bird workspace is already set up: EU region,
Germany enabled, SMS, 10 minutes, 5 tries, 60-second retry delay).

1. Make sure Bird's Verify settings also name the website
   `https://djinisbakehouse.com` (Verify → Configure), so phones offer the
   code from the text.
2. Render → the service → **Environment** → add `BIRD_API_KEY` with your
   `bk_eu1_…` key, **as a secret**. Never put the key in a file or in git.
3. Save. Render restarts the shop.
4. If `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` or `TWILIO_FROM_NUMBER` are
   still in Render, delete them — nothing reads them any more.

**How to see it worked.** The start-up log says `Text sign-in codes: Bird
Verify at https://eu1.platform.bird.com`. Sign in to your own **customer**
account, open *Profile* → *Text sign-in* → **Text me a code**, and type the
code from the SMS. Log out, then *Log in* → **Phone** → *Text me a sign-in
code instead* with the same number. Codes work once, for 10 minutes; each number gets at most one a
minute, five an hour and ten a day, and the whole shop at most 100 a day
(`SMS_DAILY_LIMIT` changes that).

**How to switch it off again.** Delete `BIRD_API_KEY` in Render. The button
and the profile panel disappear, the text endpoints answer 404, and password
sign-in carries on exactly as before.

---

## 12. "Continue with Facebook" — built, switched off

1. [developers.facebook.com](https://developers.facebook.com) → create an
   app → add **Facebook Login**.
2. In its settings, under *Valid OAuth Redirect URIs*, enter
   `https://djinisbakehouse.com/api/auth/facebook/callback`.
3. Render → Environment → `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET`.

**How to see it worked.** The log-in page shows the blue Facebook button.

---

## 13. PayPal — built, switched off

Needs a PayPal **Business** account.

1. [developer.paypal.com](https://developer.paypal.com) → Apps & Credentials
   → **Live** → create a REST app → copy the client ID and the secret.
2. In the same app: add a webhook to
   `https://djinisbakehouse.com/api/payments/paypal/webhook` and copy its
   webhook ID. The exact address and event list are in
   `docs/operations/PAYMENTS_GO_LIVE.md` (PayPal steps) — follow that page,
   it also describes a sandbox rehearsal first.
3. Render → Environment → `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`,
   `PAYPAL_ENVIRONMENT=live`, `PAYPAL_WEBHOOK_ID`.

**How to see it worked.** Checkout shows PayPal as its own option, and
`check_payments.py` reports PayPal as ready.

---

## 14. A walk through the site on a real phone

Everything was checked in a desktop browser that imitates a phone
(320, 360 and 390 pixels wide, light and dark). An imitation cannot show
how it feels in the hand. Please try these once on a real iPhone and a real
Android phone and tell me what looks or feels wrong:

- [ ] Log-in and sign-up: tapping a field must **not** zoom the page; under
      **Email** the keyboard has `@` within reach, under **Phone** it shows
      digits. The Email | Phone switch slides smoothly under your thumb.
- [ ] Sign up with a phone number (the **Phone** tab), then log in again.
- [ ] Home → a product → add to basket → basket → checkout (stop before
      paying, or pay by cash on delivery and cancel the order afterwards).
- [ ] The dashboard as admin: the four figures, the chart (tap a day — its
      amount appears), the "Table" button, the lists.
- [ ] Orders and Users at the desk: nothing runs off the side of the screen;
      on Users, a customer's card opens with one tap and scrolls to the end.
- [ ] Dark mode (the moon in the header).

---

## 15. Smaller decisions — no hurry

- **The visitor's address behind Cloudflare.** The brakes against password
  guessing count attempts per visitor address. Behind Cloudflare the shop
  should read that address from the header Cloudflare sets itself. Fix:
  Render → Environment → `CLIENT_IP_HEADER=CF-Connecting-IP` — but only
  after confirming the site really is served through Cloudflare (the notes
  in `backend/.env.example` explain why). Tell me and I will check it with
  you.
- **The server is in the USA (Oregon).** For a shop in Germany a server in
  Frankfurt would answer faster and keep the data in the EU. Moving is not a
  switch — it means a new service and a database move. Worth planning
  together, not urgent.
- **Delivery date in the orders CSV.** The export has no delivery-date
  column yet. Say the word and I add it.

---

## 16. Housekeeping

- **Backups.** This release changes nothing in the database's structure (no
  new tables or columns). Before any future change that does, make a backup
  and check it — `docs/operations/PAYMENTS_GO_LIVE.md`, step 0, says how.
- **The GitHub repository is public.** No secret is in it — keys live in
  Render only — but anyone can read the code and these documents. To make it
  private: GitHub → the repository → Settings → General → Danger Zone →
  Change visibility.
- **Your PC was short of memory while this was built** (about 2.4 GB free of
  15.8 GB). The local test shop was therefore started only for the minutes
  each check took and stopped again each time; nothing was left running. If
  you run the shop locally, close a few browser tabs first.

---

## 17. Brand colour: back to the mark's burgundy

**What is open.** The shop's colours now come from the logo itself: its
burgundy (`#851b31`) and saffron, flat fills instead of the old gradient.
But the live shop still has a brand colour **picked in Settings — teal
(`#1e7d74`)** — and a pick always wins over the shop's own colour. Until it
is reset, buttons, links and the brand strips stay teal next to the
burgundy logo. (Order statuses are not affected: they keep their own
colours whatever is picked.)

**How to fix it.** Desk → **Settings** → **Brand colour** → the first
round swatch, **"Burgundy — the mark's own"** → **Save colour**.

**How to see that it worked.** The buttons and the announcement bar are
burgundy, and `https://djinisbakehouse.com/api/store` shows
`"brand_color": null`. Any other colour can still be picked later — the
picker is unchanged.

---

## 18. Words on the site that only you can confirm

**What is open.** The wording was shortened in this release, but these
lines were deliberately left exactly as they were, because they make claims
only you know to be true — please read them and change or remove any that
are not:

- [ ] "Baked fresh every morning" — on every product's page.
- [ ] "Flour from two farms" — the home page's promise row.
- [ ] "Small-batch breads, pastries and sweet things — mixed by hand, baked
      before sunrise, gone by supper." — the footer.
- [ ] "Made with patience, baked with love — small batches out of our little
      kitchen, on your table the same day." — the home page's first lines.
- [ ] "Faster checkout, order history, first dibs on seasonal bakes." — the
      sign-up page.
- [ ] "Baked the same morning as your event" — the bulk-order page.
- [ ] "Checkout you can trust" — the home page's promise row. It says little;
      a more specific line (for example where card details are entered)
      would be a payment statement, so the choice of words is yours.

**How to fix it.** Tell me the wording you want — none of these is
editable from the desk.

---

## 19. Search engines: Search Console, then the sitemap

**What is open.** A release that lets search engines read every page
properly — each page's own title, description and address, product
information they can understand, a fuller sitemap — is ready for review but
not deployed (`docs/operations/SEO_IMPLEMENTATION_REPORT.md`). Google has
not been told about the shop: no Search Console property was set up from
here, and none can be.

**Why it matters.** Search Console is where you see whether Google has found
the pages, which searches show them, and what it could not read.

**How to fix it.**

1. **Now — no deploy needed.** [search.google.com/search-console](https://search.google.com/search-console)
   → *Add property* → **Domain** → `djinisbakehouse.com` → add the `TXT`
   record it shows where your domain's DNS is managed → *Verify*.
2. **After the search release is deployed.** *Sitemaps* → submit
   `https://djinisbakehouse.com/sitemap.xml`; *URL Inspection* → test the
   home page and one product page → *Request indexing*.
3. **Your decision.** A Google Business Profile as a delivery business for
   28359, with the home address hidden — the report's section 9 says what
   Google allows.

**How to see it worked.** The sitemap shows "Success", and over the
following days *Pages → Indexed* grows. Nothing guarantees indexing, and it
takes days to weeks.

---

## What could not be verified from here

So that nothing is taken for proven that was not:

| Thing | Why not | Covered by |
| --- | --- | --- |
| A real Stripe payment, refund or dispute | no Stripe credentials on the build machine | item 1 |
| A real PayPal payment | no credentials; PayPal is off | item 13 |
| A real text message through Bird | no live Bird call was made: the tests use a stand-in Bird, and the key is not set | item 11 |
| Emails arriving in an inbox | the mail settings live in Render | item 4 |
| How the site feels on a real phone | checked only in an imitated phone | item 14 |
| Anything inside your Stripe or Render accounts | only you can open them | items 1, 2, 4, 7 |
