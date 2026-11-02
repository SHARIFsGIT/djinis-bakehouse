import { Flame, ShieldCheck, Sparkles, Star, Truck, Users, Wheat } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import client from "../api/client";
import { ProductGridSkeleton } from "../components/Loading";
import PhotoTile from "../components/PhotoTile";
import ProductCard from "../components/ProductCard";
import { productInitial } from "../lib/format";

// Shown until /store answers (and whenever the admin hasn't picked one)
const DEFAULT_HERO = "/static/site/baker.jpg";

const MARQUEE = [
  "FRESH EVERY MORNING",
  "TWO-DAY CROISSANTS",
  "KETTLE-BOILED BAGELS",
  "STONE-GROUND FLOUR",
  "CULTURED FRENCH BUTTER",
  "WEEKEND DOUGHNUTS",
];

const PROMISES = [
  {
    Icon: Flame,
    title: "Out of the oven at 6",
    text: "Baked the morning it ships — never yesterday's loaf.",
  },
  {
    Icon: Truck,
    title: "Free delivery over $50",
    text: "Evening runs across Sweet Valley, Tue – Sat.",
  },
  {
    Icon: ShieldCheck,
    title: "Checkout you can trust",
    text: "Card or cash, and a full refund if you cancel before baking.",
  },
  {
    Icon: Wheat,
    title: "Flour from two farms away",
    text: "Stone-ground, additive-free, delivered in sacks not silos.",
  },
];

/** Stars for a testimonial's optional 1–5 rating. */
function Stars({ rating }) {
  if (!rating) return null;
  return (
    <div className="flex gap-0.5" aria-label={`${rating} out of 5`}>
      {Array.from({ length: 5 }, (_, i) => (
        <Star
          key={i}
          size={15}
          aria-hidden="true"
          className={i < rating ? "fill-accent text-accent" : "text-base-300"}
        />
      ))}
    </div>
  );
}

/** A word from the neighborhood — photo when the baker set one, initial otherwise. */
function TestimonialFigure({ t }) {
  return (
    <figure className="glass rounded-box p-6 shadow-sm ring-1 ring-base-300/60">
      <Stars rating={t.rating} />
      <blockquote className="mt-3 leading-relaxed opacity-85">
        “{t.quote}”
      </blockquote>
      <figcaption className="mt-5 flex items-center gap-3">
        {t.avatar_url ? (
          <img
            src={t.avatar_url}
            alt=""
            loading="lazy"
            className="h-11 w-11 rounded-full object-cover ring-1 ring-base-300"
          />
        ) : (
          <span className="brand-gradient flex h-11 w-11 items-center justify-center rounded-full text-secondary-content font-display font-bold">
            {productInitial(t.name)}
          </span>
        )}
        <div>
          <p className="font-semibold">{t.name}</p>
          {t.role && <p className="text-xs opacity-60">{t.role}</p>}
        </div>
      </figcaption>
    </figure>
  );
}

export default function Home() {
  const [categories, setCategories] = useState([]);
  const [featured, setFeatured] = useState(null);
  const [todayNote, setTodayNote] = useState("");
  const [heroImage, setHeroImage] = useState(DEFAULT_HERO);
  const [testimonials, setTestimonials] = useState([]);
  const [comingSoon, setComingSoon] = useState([]);

  useEffect(() => {
    client
      .get("/products/categories")
      .then(({ data }) => setCategories(data))
      .catch(() => {});
    client
      .get("/products/featured", { params: { limit: 8 } })
      .then(({ data }) => setFeatured(data))
      .catch(() => setFeatured([]));
    client
      .get("/store")
      .then(({ data }) => {
        setTodayNote(data.today_note);
        setHeroImage(data.hero_image || DEFAULT_HERO);
      })
      .catch(() => {});
    // The baker's two curatable bands — empty lists simply hide the sections
    client
      .get("/testimonials")
      .then(({ data }) => setTestimonials(data))
      .catch(() => setTestimonials([]));
    client
      .get("/coming-soon")
      .then(({ data }) => setComingSoon(data))
      .catch(() => setComingSoon([]));
  }, []);

  return (
    <div>
      {/* ---------------------------------------------------------------- */}
      {/* Hero                                                              */}
      {/* ---------------------------------------------------------------- */}
      <section className="relative overflow-hidden">
        <div className="mx-auto grid max-w-7xl items-center gap-12 px-4 py-14 md:grid-cols-2 md:py-20">
          <div className="rise-in">
            <p className="badge badge-outline badge-secondary px-3 py-3 text-xs font-bold tracking-widest text-white uppercase">
              Est. 2016 · Small-batch bakery
            </p>
            <h1 className="font-display mt-5 text-5xl leading-[1.05] font-semibold md:text-6xl">
              Butter, flour &<br />a little{" "}
              <em className="text-secondary italic">patience</em>
            </h1>
            <p className="mt-6 max-w-md text-lg leading-relaxed opacity-80">
              Every loaf proofs overnight, every croissant is laminated by hand,
              and the ovens have been on since 4 a.m. Come see what&rsquo;s
              fresh.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/products" className="btn btn-primary btn-lg shadow-md">
                Shop the case
              </Link>
              <Link
                to="/bulk"
                className="btn btn-ghost btn-lg border border-base-300 transition hover:bg-base-200"
              >
                <Users size={20} />
                Bulk order
              </Link>
            </div>
            <div className="mt-10 flex gap-8 text-sm">
              <div>
                <p className="font-display text-2xl font-bold">25</p>
                <p className="opacity-60">bakes daily</p>
              </div>
              <div>
                <p className="font-display text-2xl font-bold">4.8</p>
                <p className="opacity-60">customer rating</p>
              </div>
              <div>
                <p className="font-display text-2xl font-bold">6am</p>
                <p className="opacity-60">first batch out</p>
              </div>
            </div>
          </div>

          <div className="relative mx-auto w-full max-w-sm md:max-w-none">
            {/* One frame: the photo with the today-note attached as its own
                bottom piece — the strip is part of the picture, not a sticker
                floating over it */}
            <div className="overflow-hidden rounded-box shadow-xl ring-1 ring-base-300/60">
              <img
                src={heroImage}
                alt="Djini behind the counter in her chef's coat"
                className="aspect-6/5 w-full object-cover"
              />
              {todayNote && (
                <div className="brand-gradient text-primary-content">
                  <div className="flex items-center gap-3 px-5 py-3.5">
                    <Flame
                      size={20}
                      className="shrink-0 text-accent"
                      aria-hidden="true"
                    />
                    <div className="min-w-0">
                      <p className="text-[0.65rem] font-bold tracking-[0.22em] uppercase opacity-70">
                        Today
                      </p>
                      <p className="font-display truncate font-semibold">
                        {todayNote}
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* Promises                                                          */}
      {/* ---------------------------------------------------------------- */}
      <section className="border-y border-base-300/60 bg-base-200/40">
        <div className="mx-auto grid max-w-7xl gap-6 px-4 py-8 sm:grid-cols-2 lg:grid-cols-4">
          {PROMISES.map(({ Icon, title, text }) => (
            <div key={title} className="flex items-start gap-3.5">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-secondary/15 text-secondary">
                <Icon size={20} />
              </span>
              <div>
                <p className="font-display font-semibold">{title}</p>
                <p className="mt-0.5 text-sm opacity-70">{text}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* Marquee band                                                      */}
      {/* ---------------------------------------------------------------- */}
      <section
        className="brand-gradient overflow-hidden py-3 text-primary-content"
        aria-hidden="true"
      >
        <div className="marquee-track flex w-max gap-10 whitespace-nowrap">
          {[...MARQUEE, ...MARQUEE, ...MARQUEE, ...MARQUEE].map((item, i) => (
            <span
              key={i}
              className="font-display flex items-center gap-10 text-sm font-bold tracking-[0.2em] uppercase"
            >
              {item} <span className="opacity-50">·</span>
            </span>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* Categories                                                        */}
      {/* ---------------------------------------------------------------- */}
      <section className="mx-auto max-w-7xl px-4 py-16">
        <div className="mb-8 flex items-end justify-between gap-4">
          <div>
            <h2 className="font-display text-3xl font-semibold md:text-4xl">
              Browse the shelves
            </h2>
            <p className="mt-2 opacity-70">
              Every corner of the shop, restocked every morning.
            </p>
          </div>
          <Link
            to="/products"
            className="link link-secondary hidden text-sm font-semibold sm:inline"
          >
            See everything
          </Link>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          {categories.slice(0, 6).map((c, i) => (
            <Link
              key={c.slug}
              to={`/products?category=${c.slug}`}
              className="group rise-in relative overflow-hidden rounded-box shadow-sm ring-1 ring-base-300/60"
              style={{ animationDelay: `${i * 0.06}s` }}
            >
              <PhotoTile
                src={c.image_url}
                name={c.name}
                className="aspect-4/3 w-full"
              />
              <div className="absolute inset-x-0 bottom-0 bg-linear-to-t from-base-100 via-base-100/85 to-transparent p-4 pt-12">
                <p className="font-display text-xl font-semibold">{c.name}</p>
                <p className="text-xs opacity-70">
                  {c.product_count} {c.product_count === 1 ? "item" : "items"}{" "}
                  in the case
                </p>
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* Featured                                                          */}
      {/* ---------------------------------------------------------------- */}
      <section className="bg-base-200/50 py-16">
        <div className="mx-auto max-w-7xl px-4">
          <div className="mb-8 flex items-end justify-between gap-4">
            <div>
              <h2 className="font-display text-3xl font-semibold md:text-4xl">
                Fresh from the oven
              </h2>
              <p className="mt-2 opacity-70">
                The neighborhood&rsquo;s favorites, straight off the peel.
              </p>
            </div>
          </div>
          {featured === null ? (
            <ProductGridSkeleton />
          ) : (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
              {featured.map((p) => (
                <ProductCard key={p.id} product={p} />
              ))}
            </div>
          )}
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* Coming soon — the baker's look-ahead band, curated under Settings */}
      {/* ---------------------------------------------------------------- */}
      {comingSoon.length > 0 && (
        <section className="mx-auto max-w-7xl px-4 py-16">
          <div className="mb-8 flex items-end justify-between gap-4">
            <div>
              <p className="flex items-center gap-2 text-xs font-bold tracking-[0.25em] text-secondary uppercase">
                <Sparkles size={14} aria-hidden="true" />
                On its way
              </p>
              <h2 className="font-display mt-2 text-3xl font-semibold md:text-4xl">
                Coming soon to the counter
              </h2>
              <p className="mt-2 opacity-70">
                What Djini is testing, timing and promising for the shelves next.
              </p>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {comingSoon.map((item, i) => (
              <article
                key={item.id}
                className="group rise-in overflow-hidden rounded-box shadow-sm ring-1 ring-base-300/60"
                style={{ animationDelay: `${i * 0.06}s` }}
              >
                <PhotoTile
                  src={item.image_url}
                  name={item.title}
                  className="aspect-16/10 w-full"
                />
                <div className="bg-base-200/60 p-5">
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="font-display text-lg font-semibold">
                      {item.title}
                    </h3>
                    {item.eta_label && (
                      <span className="badge badge-secondary badge-sm shrink-0 text-secondary-content">
                        {item.eta_label}
                      </span>
                    )}
                  </div>
                  {item.description && (
                    <p className="mt-2 text-sm leading-relaxed opacity-75">
                      {item.description}
                    </p>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      {/* ---------------------------------------------------------------- */}
      {/* Testimonials — the baker's picks, editable under Settings         */}
      {/* ---------------------------------------------------------------- */}
      {testimonials.length > 0 && (
        <section className="bg-base-200/50 py-16">
          <div className="mx-auto max-w-7xl px-4">
            <h2 className="font-display mb-8 text-center text-3xl font-semibold md:text-4xl">
              Word around the neighborhood
            </h2>
            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
              {testimonials.map((t) => (
                <TestimonialFigure key={t.id} t={t} />
              ))}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
