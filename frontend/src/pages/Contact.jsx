import { ArrowUpRight, Clock, Mail, MapPin, Phone } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Loading } from "../components/Loading";
import { describeArea } from "../lib/delivery";
import { fetchStore } from "../lib/store";

/* Where the counter is, when the ovens are on, how to reach the bakery.
   Every line on this page is the baker's own, from Settings — the same
   details the footer carries, with room to breathe. Nothing is filled in
   on the shop's behalf: a line that hasn't been written simply isn't here. */

/** "Tue – Fri: 7:00 – 18:00" → ["Tue – Fri", "7:00 – 18:00"]; a line with
    no label ("Closed on public holidays") stays whole. The split is at the
    first colon that follows a letter — never inside a time. */
const hoursRow = (line) => {
  const cut = line.search(/[A-Za-zÀ-ÿ.]\s*:/);
  if (cut === -1) return [line, ""];
  const colon = line.indexOf(":", cut);
  return [line.slice(0, colon).trim(), line.slice(colon + 1).trim()];
};

export default function Contact() {
  const [store, setStore] = useState(null);

  useEffect(() => {
    fetchStore()
      .then(setStore)
      .catch(() => setStore({}));
  }, []);

  if (store === null) return <Loading label="Finding the counter…" />;

  const address = store.contact_address?.trim();
  const phone = store.contact_phone?.trim();
  const email = store.contact_email?.trim();
  const hours = (store.opening_hours ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const area = store.delivery_postal_codes ?? [];
  const nothingYet = !address && !phone && !email && hours.length === 0;

  return (
    <div className="mx-auto max-w-5xl px-4 py-12">
      <h1 className="font-display text-4xl font-semibold md:text-5xl">Find us</h1>
      <p className="mt-3 max-w-xl text-lg leading-relaxed opacity-75">
        Where the counter is, when the ovens are on, and the quickest way to
        reach the bakery.
      </p>

      {nothingYet ? (
        <p className="mt-10 max-w-xl rounded-box border border-dashed border-base-300 p-6 opacity-80">
          The bakery hasn&rsquo;t published its address and hours here yet.
          For anything about an order, the order&rsquo;s own page is the
          fastest way to us.
        </p>
      ) : (
        <div className="mt-10 grid gap-10 md:grid-cols-[1.1fr_1fr]">
          <div className="space-y-8">
            {address && (
              <section>
                <h2 className="flex items-center gap-2.5 font-display text-xl font-semibold">
                  <MapPin size={19} className="text-secondary" aria-hidden="true" />
                  The counter
                </h2>
                <address className="mt-3 text-lg leading-relaxed not-italic">{address}</address>
                <a
                  href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-outline btn-sm mt-4"
                >
                  Open in Maps
                  <ArrowUpRight size={15} aria-hidden="true" />
                </a>
              </section>
            )}

            {(phone || email) && (
              <section>
                <h2 className="font-display text-xl font-semibold">Say hello</h2>
                <ul className="mt-3 space-y-2.5 text-lg">
                  {phone && (
                    <li className="flex items-center gap-3">
                      <Phone size={18} className="shrink-0 text-secondary" aria-hidden="true" />
                      <a href={`tel:${phone.replace(/[^+\d]/g, "")}`} className="link link-hover">{phone}</a>
                    </li>
                  )}
                  {email && (
                    <li className="flex items-center gap-3">
                      <Mail size={18} className="shrink-0 text-secondary" aria-hidden="true" />
                      <a href={`mailto:${email}`} className="link link-hover break-all">{email}</a>
                    </li>
                  )}
                </ul>
              </section>
            )}
          </div>

          {hours.length > 0 && (
            <section className="rounded-box bg-base-200/60 p-6">
              <h2 className="flex items-center gap-2.5 font-display text-xl font-semibold">
                <Clock size={19} className="text-secondary" aria-hidden="true" />
                Bake hours
              </h2>
              <dl className="mt-4 divide-y divide-base-300/70">
                {hours.map((line, i) => {
                  const [days, times] = hoursRow(line);
                  return (
                    <div key={`${i}-${line}`} className="flex items-baseline justify-between gap-6 py-2.5">
                      <dt className="font-semibold">{days}</dt>
                      {times && <dd className="figures text-right opacity-80">{times}</dd>}
                    </div>
                  );
                })}
              </dl>
            </section>
          )}
        </div>
      )}

      <div className="dash-divider mt-12 grid gap-8 pt-10 sm:grid-cols-2">
        <div>
          <h2 className="font-display text-xl font-semibold">Delivery</h2>
          <p className="mt-2 leading-relaxed opacity-80">
            {area.length > 0
              ? `We deliver to postal codes ${describeArea(area, 12)}.`
              : "Delivery goes to the address you give at checkout."}
          </p>
          <Link to="/products" className="link link-secondary mt-3 inline-block font-semibold">
            Explore our bakes
          </Link>
        </div>
        <div>
          <h2 className="font-display text-xl font-semibold">Feeding a crowd?</h2>
          <p className="mt-2 leading-relaxed opacity-80">
            Birthdays, weddings, the Friday stand-up — tell us the date and
            the headcount and we come back with a quote.
          </p>
          <Link to="/bulk" className="link link-secondary mt-3 inline-block font-semibold">
            Ask about a bulk order
          </Link>
        </div>
      </div>
    </div>
  );
}
