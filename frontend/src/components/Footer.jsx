import { Mail, MapPin, Phone, Send } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import client from "../api/client";
import { useToast } from "../context/ToastContext";
import useStore from "../hooks/useStore";

/* Contact details and bake hours come from the shop's Settings tab, so the
 * real address and phone live in the database — nothing fictional ships.
 * Until the baker fills them in, the lines simply don't render. */

export default function Footer() {
  const toast = useToast();
  const store = useStore();
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState(false);

  const hours = (store?.opening_hours ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const address = store?.contact_address?.trim();
  const phone = store?.contact_phone?.trim();
  const contactEmail = store?.contact_email?.trim();
  const hasContact = Boolean(address || phone || contactEmail);

  const subscribe = async (e) => {
    e.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || sending) return;
    setSending(true);
    try {
      const { data } = await client.post("/newsletter", { email: trimmed, source: "footer" });
      setDone(true);
      toast.success(
        data.already_subscribed
          ? "You're already on the list — see you Friday"
          : "You're on the list — WELCOME10 is waiting at checkout"
      );
    } catch {
      toast.error("That didn't go through — try again in a moment");
    } finally {
      setSending(false);
    }
  };

  return (
    <footer className="mt-20 border-t border-base-300/60 bg-base-200/60">
      {/* Three columns or four — the bake-hours column exists only once the
          baker has written hours, and the row is spread over what is there
          instead of leaving a quarter of the footer empty. */}
      <div
        className={`mx-auto grid max-w-7xl gap-10 px-4 py-12 sm:grid-cols-2 ${
          hours.length > 0 ? "lg:grid-cols-4" : "lg:grid-cols-3"
        }`}
      >
        <div>
          <div className="flex items-center gap-2.5">
            <img src="/static/site/logo-128.webp" alt="Djini's Bakehouse" className="h-9 w-9 object-contain" />
            <p className="font-display text-xl font-bold">
              Djini&rsquo;s <span className="italic text-secondary">Bakehouse</span>
            </p>
          </div>
          <p className="mt-3 text-sm leading-relaxed opacity-75">
            Small-batch breads, pastries and sweet things — mixed by hand,
            baked before sunrise, gone by supper.
          </p>
        </div>

        <div>
          <p className="mb-3 font-display font-semibold">The counter</p>
          <ul className="space-y-2 text-sm opacity-80">
            <li><Link to="/products" className="link link-hover">Explore Our Bakes</Link></li>
            <li><Link to="/products?category=breads" className="link link-hover">Daily breads</Link></li>
            <li><Link to="/products?category=desserts" className="link link-hover">Desserts</Link></li>
            <li><Link to="/cart" className="link link-hover">Your basket</Link></li>
            <li><Link to="/bulk" className="link link-hover">Bulk &amp; event orders</Link></li>
            <li><Link to="/contact" className="link link-hover">Find us</Link></li>
          </ul>
        </div>

        {hours.length > 0 && (
          <div>
            <p className="mb-3 font-display font-semibold">Bake hours</p>
            <ul className="space-y-2 text-sm opacity-80">
              {hours.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        )}

        <div>
          {hasContact && (
            <p className="mb-3 font-display font-semibold">Say hello</p>
          )}
          <ul className={`space-y-2.5 text-sm opacity-80 ${hasContact ? "" : "hidden"}`}>
            {address && (
              <li className="flex items-start gap-2.5">
                <MapPin size={16} className="mt-0.5 shrink-0 opacity-70" aria-hidden="true" />
                <span>{address}</span>
              </li>
            )}
            {/* Tappable: on a phone, a number is something to call. */}
            {phone && (
              <li className="flex items-center gap-2.5">
                <Phone size={16} className="shrink-0 opacity-70" aria-hidden="true" />
                <a href={`tel:${phone.replace(/[^+\d]/g, "")}`} className="link link-hover">{phone}</a>
              </li>
            )}
            {contactEmail && (
              <li className="flex items-center gap-2.5">
                <Mail size={16} className="shrink-0 opacity-70" aria-hidden="true" />
                <a href={`mailto:${contactEmail}`} className="link link-hover break-all">{contactEmail}</a>
              </li>
            )}
          </ul>

          {/* Under the contact lines when there are any; at the head of the
              column, level with its neighbours, when there are none. */}
          <form onSubmit={subscribe} className={hasContact ? "mt-5" : ""}>
            <label
              className={`mb-2 block ${hasContact ? "text-xs font-semibold" : "font-display font-semibold"}`}
              htmlFor="newsletter-email"
            >
              First peek at the weekend case
            </label>
            {done ? (
              <p className="rounded-box border border-dashed border-secondary/40 bg-secondary/10 px-3 py-2 text-xs font-semibold text-secondary">
                You&rsquo;re on the list — WELCOME10 is yours
              </p>
            ) : (
              <div className="join w-full max-w-xs">
                <input
                  id="newsletter-email"
                  type="email"
                  required
                  className="input input-bordered input-sm join-item w-full bg-base-100"
                  placeholder="Your email"
                  autoComplete="email"
                  enterKeyHint="send"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
                <button
                  type="submit"
                  className="btn btn-secondary btn-sm join-item text-secondary-content"
                  disabled={sending}
                  aria-label="Subscribe"
                >
                  {sending ? (
                    <span className="loading loading-spinner loading-xs" aria-hidden="true" />
                  ) : (
                    <Send size={15} aria-hidden="true" />
                  )}
                </button>
              </div>
            )}
            <p className="mt-1.5 text-xs opacity-65">
              One email a week, Friday mornings. No spam, ever —{" "}
              <Link to="/privacy" className="link">how we handle your address</Link>.
            </p>
          </form>
        </div>
      </div>

      {/* Each entry is one unbreakable phrase: on a narrow screen the row
          wraps between entries, never through the middle of "Refunds &
          cancellations". The row ends clear of a phone's home indicator, of
          whatever a page has docked to the bottom edge (--dock) and of the
          back-to-top button when it is out (--top-button) — so the last
          links on the page can always be scrolled into view and tapped. */}
      <ul
        className="flex flex-wrap items-center justify-center gap-x-5 gap-y-1.5 border-t border-base-300/60 px-4 py-4 text-xs opacity-70"
        style={{
          paddingBottom:
            "calc(1rem + env(safe-area-inset-bottom, 0px) + var(--dock, 0px) + var(--top-button, 0px))",
        }}
      >
        <li className="whitespace-nowrap">© {new Date().getFullYear()} Djini&rsquo;s Bakehouse</li>
        <li className="whitespace-nowrap"><Link to="/privacy" className="link link-hover">Privacy</Link></li>
        <li className="whitespace-nowrap"><Link to="/terms" className="link link-hover">Terms</Link></li>
        <li className="whitespace-nowrap">
          <Link to="/refunds" className="link link-hover">Refunds &amp; cancellations</Link>
        </li>
        {/* Only once the baker has published one — never a link to an
            empty legal page. */}
        {store?.legal_notice_published && (
          <li className="whitespace-nowrap"><Link to="/imprint" className="link link-hover">Legal notice</Link></li>
        )}
      </ul>
    </footer>
  );
}
