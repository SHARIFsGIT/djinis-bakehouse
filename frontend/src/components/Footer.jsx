import { Mail, MapPin, Phone, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import client from "../api/client";
import { useToast } from "../context/ToastContext";

/* Contact details and bake hours come from the shop's Settings tab, so the
 * real address and phone live in the database — nothing fictional ships.
 * Until the baker fills them in, the lines simply don't render. */

export default function Footer() {
  const toast = useToast();
  const [store, setStore] = useState(null);
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    client
      .get("/store")
      .then(({ data }) => setStore(data))
      .catch(() => setStore({}));
  }, []);

  const hours = (store?.opening_hours ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const address = store?.contact_address?.trim();
  const phone = store?.contact_phone?.trim();
  const contactEmail = store?.contact_email?.trim();

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
      <div className="mx-auto grid max-w-7xl gap-10 px-4 py-12 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <div className="flex items-center gap-2.5">
            <img src="/static/site/logo.png" alt="Djini's Bakehouse" className="h-9 w-9 object-contain" />
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
          {(address || phone || contactEmail) && (
            <p className="mb-3 font-display font-semibold">Say hello</p>
          )}
          <ul className="space-y-2.5 text-sm opacity-80">
            {address && (
              <li className="flex items-start gap-2.5">
                <MapPin size={16} className="mt-0.5 shrink-0 opacity-60" />
                <span>{address}</span>
              </li>
            )}
            {phone && (
              <li className="flex items-center gap-2.5">
                <Phone size={16} className="shrink-0 opacity-60" />
                <span>{phone}</span>
              </li>
            )}
            {contactEmail && (
              <li className="flex items-center gap-2.5">
                <Mail size={16} className="shrink-0 opacity-60" />
                <span>{contactEmail}</span>
              </li>
            )}
          </ul>

          <form onSubmit={subscribe} className="mt-5">
            <label className="label mb-1.5 text-xs font-semibold" htmlFor="newsletter-email">
              First peek at the weekend case
            </label>
            {done ? (
              <p className="rounded-box border border-dashed border-secondary/40 bg-secondary/10 px-3 py-2 text-xs font-semibold text-secondary">
                You&rsquo;re on the list — WELCOME10 is yours
              </p>
            ) : (
              <div className="join">
                <input
                  id="newsletter-email"
                  type="email"
                  required
                  className="input input-bordered input-sm join-item w-full bg-base-100"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
                <button
                  type="submit"
                  className="btn btn-secondary btn-sm join-item text-secondary-content"
                  disabled={sending}
                  aria-label="Subscribe"
                >
                  {sending ? "…" : <Send size={15} />}
                </button>
              </div>
            )}
            <p className="mt-1.5 text-xs opacity-55">One email a week, Friday mornings. No spam, ever.</p>
          </form>
        </div>
      </div>

      <div className="border-t border-base-300/60 py-4 text-center text-xs opacity-60">
        © {new Date().getFullYear()} Djini&rsquo;s Bakehouse
        <span className="mx-2" aria-hidden="true">·</span>
        <Link to="/privacy" className="link link-hover">Privacy</Link>
        <span className="mx-2" aria-hidden="true">·</span>
        <Link to="/terms" className="link link-hover">Terms</Link>
        <span className="mx-2" aria-hidden="true">·</span>
        <Link to="/refunds" className="link link-hover">Refunds &amp; cancellations</Link>
      </div>
    </footer>
  );
}
