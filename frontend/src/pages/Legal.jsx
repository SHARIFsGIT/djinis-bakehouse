import { useEffect, useState } from "react";
import { Link } from "react-router";
import client from "../api/client";

/* The shop's legal pages. Written to match how the site actually behaves —
 * cash on delivery, free cancellation until baking starts, allergens on
 * every product page — so nothing here over-promises. Review before launch
 * and whenever local rules change. */

function useContactEmail() {
  // The baker's real contact email from Settings, when it's been filled in —
  // until then these pages point at the footer instead of promising a
  // mailbox that may not exist yet.
  const [email, setEmail] = useState(null);
  useEffect(() => {
    client
      .get("/store")
      .then(({ data }) => setEmail(data.contact_email || null))
      .catch(() => setEmail(null));
  }, []);
  return email;
}

function ReachUs({ email }) {
  if (email) {
    return (
      <>reach us at{" "}
        <a href={`mailto:${email}`} className="link link-secondary">{email}</a></>
    );
  }
  return <>reach us through the contact details in the{" "}
    <Link to="/" className="link link-secondary">site footer</Link></>;
}

function LegalPage({ eyebrow, title, updated, children }) {
  return (
    <div className="mx-auto max-w-3xl px-4 py-12">
      <p className="text-xs font-bold uppercase tracking-[0.2em] text-secondary">
        {eyebrow}
      </p>
      <h1 className="mt-2 font-display text-4xl font-semibold">{title}</h1>
      <p className="mt-2 text-xs opacity-55">Last updated: {updated}</p>
      <div className="mt-8 space-y-6 text-[0.95rem] leading-relaxed opacity-85">
        {children}
      </div>
    </div>
  );
}

function Section({ heading, children }) {
  return (
    <section className="space-y-2">
      <h2 className="font-display text-xl font-semibold opacity-100">{heading}</h2>
      {children}
    </section>
  );
}

const UPDATED = "November 2026";

export function PrivacyPolicy() {
  const contactEmail = useContactEmail();
  return (
    <LegalPage eyebrow="The paperwork" title="Privacy policy" updated={UPDATED}>
      <p>
        We run a bakery, not a data business. This page explains what we keep
        about you, why, and the choices you have.
      </p>

      <Section heading="What we collect">
        <ul className="list-disc space-y-1.5 pl-5">
          <li><strong>Account details</strong> — your name, email address and a hashed password when you register.</li>
          <li><strong>Order details</strong> — the delivery address, recipient name and phone number attached to your orders, and what you bought.</li>
          <li><strong>Reviews and messages</strong> — anything you write on a review or the note field of an order.</li>
          <li><strong>Newsletter email</strong> — your email address alone, if you sign up for the Friday list.</li>
        </ul>
      </Section>

      <Section heading="What we do with it">
        <p>
          We use your information to bake and deliver your orders, to keep your
          account and address book working, to email order receipts and password
          resets, and to send the newsletter if you asked for it. Online card
          payments are processed by Stripe as our payment service provider —
          card details are entered on Stripe's own pages and never reach our
          servers. That is the whole list. We never sell or rent your details
          to anyone.
        </p>
      </Section>

      <Section heading="Cookies and browser storage">
        <p>
          We don't run advertising trackers. The site keeps a small amount of
          storage in your own browser — your basket, your theme choice and your
          login session. Clearing your browser data removes all of it.
        </p>
      </Section>

      <Section heading="How long we keep things">
        <p>
          Order records stay with us for as long as we need them for accounting
          and to answer questions about past orders. Your account stays until
          you ask us to remove it. Newsletter addresses stay until you
          unsubscribe.
        </p>
      </Section>

      <Section heading="Your choices">
        <p>
          You can correct or delete your account details from your{" "}
          <Link to="/account/profile" className="link link-secondary">profile page</Link>,
          unsubscribe from the newsletter at any time — a reply to any of our
          emails or a quick message takes you off the list — and ask us to
          close your account whenever you like. Questions or requests?{" "}
          <ReachUs email={contactEmail} />
        </p>
      </Section>
    </LegalPage>
  );
}

export function TermsOfService() {
  return (
    <LegalPage eyebrow="The paperwork" title="Terms of service" updated={UPDATED}>
      <p>
        These terms cover the orders you place through this website. By placing
        an order you agree to them.
      </p>

      <Section heading="Orders and prices">
        <p>
          Everything you add to a basket is re-checked at checkout — the price
          and availability on our side at that moment are what apply. Taxes and
          delivery are calculated and shown before you confirm.
        </p>
      </Section>

      <Section heading="Payment">
        <p>
          Whatever the checkout offers is what we take: card — paid online
          through Stripe's secure checkout page, where your card details are
          entered on Stripe's site and never touch ours — or cash on delivery,
          settled with the courier at your door.
        </p>
      </Section>

      <Section heading="Delivery">
        <p>
          We bake in small batches and deliver on the schedule shown at
          checkout. A delivery date you pick is a target window rather than a
          minute-precise appointment — someone should be available to receive
          the box. If a delivery fails because nobody is present, we will
          contact you to arrange another attempt.
        </p>
      </Section>

      <Section heading="Coupons">
        <p>
          One coupon per order. Codes carry any minimum-basket rule shown with
          them, and the shop may retire a code at any time — an active code at
          checkout always applies.
        </p>
      </Section>

      <Section heading="Allergens">
        <p>
          Every product page lists its ingredients and allergens. Our kitchen
          handles gluten, dairy, eggs, nuts, sesame and other allergens, so we
          cannot guarantee a trace-free environment. If you have an allergy,
          contact us before ordering and we will tell you honestly what we can
          and cannot promise.
        </p>
      </Section>

      <Section heading="Changes">
        <p>
          If we change these terms we will update this page and the date above.
          The terms that applied are the ones in force when you placed your
          order.
        </p>
      </Section>
    </LegalPage>
  );
}

export function RefundPolicy() {
  const contactEmail = useContactEmail();
  return (
    <LegalPage eyebrow="The paperwork" title="Refunds & cancellations" updated={UPDATED}>
      <Section heading="Cancelling an order">
        <p>
          You can cancel from your order page while the order is pending,
          confirmed or baking — free of charge until the ovens start. Once
          baking has begun we may keep a late-cancellation fee; the exact
          amount is always shown to you <em>before</em> you confirm a
          cancellation, so nothing is a surprise.
        </p>
      </Section>

      <Section heading="Something wrong with the box?">
        <p>
          If an item is missing, wrong or not right when it arrives,{" "}
          <ReachUs email={contactEmail} /> within 24 hours. We will
          replace the item or refund it — your choice. Because we are talking
          about fresh food, we may ask for a photo of the problem.
        </p>
      </Section>

      <Section heading="How money comes back to you">
        <p>
          Card payments are refunded to the same card automatically — usually
          within a few business days, the exact timing your bank's. Cash
          payments are refunded in cash or by a transfer to an account you
          nominate, arranged with you directly when the refund is agreed.
        </p>
      </Section>
    </LegalPage>
  );
}
