import { BadgePercent, Plus, Truck } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import client from "../api/client";
import EmptyState from "../components/EmptyState";
import PhotoTile from "../components/PhotoTile";
import PriceTag from "../components/PriceTag";
import QuantityInput from "../components/QuantityInput";
import { useAuth } from "../context/AuthContext";
import { useCart } from "../context/CartContext";
import { useToast } from "../context/ToastContext";
import { getStoredCoupon, storeCoupon } from "../lib/coupon";
import {
  FREE_SHIPPING_THRESHOLD,
  computeTotals,
  money,
  taxLabel,
} from "../lib/format";

/** One live voucher: the saving it offers and whether this basket earns it. */
function VoucherCard({ voucher, subtotal, applied, onApply, onRemove }) {
  const min = voucher.min_subtotal || 0;
  const unlocked = subtotal >= min;
  const away = Math.max(0, min - subtotal);
  const saving = (subtotal * voucher.percent_off) / 100;

  return (
    <div
      className={`rounded-xl border p-3.5 transition ${
        applied
          ? "border-secondary bg-secondary/10"
          : "border-base-300 bg-base-200/50"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className={`badge badge-sm font-bold ${
              applied ? "badge-secondary text-secondary-content" : "badge-outline border-secondary text-secondary"
            }`}
          >
            <BadgePercent size={12} aria-hidden="true" />
            {voucher.code}
          </span>
        </div>
        {applied ? (
          <button
            type="button"
            className="btn btn-ghost btn-xs"
            onClick={onRemove}
          >
            Remove
          </button>
        ) : (
          <button
            type="button"
            className={`btn btn-xs ${unlocked ? "btn-primary" : "btn-ghost border border-base-300"}`}
            onClick={() => onApply(voucher)}
          >
            {unlocked ? "Apply" : "Aim for it"}
          </button>
        )}
      </div>
      {voucher.description && (
        <p className="mt-1 text-xs opacity-65">{voucher.description}</p>
      )}
      <p className="mt-2 text-xs font-semibold">
        {min > 0 && !unlocked && (
          <span className="text-secondary">
            Add {money(away)} more to unlock
          </span>
        )}
        {min > 0 && unlocked && (
          <span className="text-success">
            Unlocked · saves {money(saving)}
          </span>
        )}
        {min === 0 && (
          <span className="text-success">
            Any basket · saves {money(saving)}
          </span>
        )}
      </p>
    </div>
  );
}

export default function Cart() {
  const { items, setQuantity, remove, add, subtotal, count } = useCart();
  const { user } = useAuth();
  const toast = useToast();
  const [bestRated, setBestRated] = useState([]);
  const [vouchers, setVouchers] = useState([]);
  const [appliedCode, setAppliedCode] = useState(getStoredCoupon());

  // One-tap add-ons — the best-rated bakes not already in the basket
  useEffect(() => {
    client
      .get("/products", { params: { sort: "rating", per_page: 8 } })
      .then(({ data }) => setBestRated(data.products))
      .catch(() => setBestRated([]));
    // The live voucher board — codes the shop is actively offering
    client
      .get("/coupons")
      .then(({ data }) => setVouchers(data))
      .catch(() => setVouchers([]));
  }, []);

  const applyVoucher = (voucher) => {
    const min = voucher.min_subtotal || 0;
    if (subtotal >= min) {
      setAppliedCode(voucher.code);
      storeCoupon(voucher.code);
      toast.success(`${voucher.code} applied`);
    } else {
      // Not earned yet: point at the nearest bake that crosses the line
      setAppliedCode("");
      storeCoupon("");
      toast.info(
        `${voucher.code} unlocks at ${money(min)} — add ${money(min - subtotal)} more`,
      );
    }
  };

  const removeVoucher = () => {
    setAppliedCode("");
    storeCoupon("");
    toast.info("Voucher removed");
  };

  if (items.length === 0) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-8">
        <EmptyState
          title="Your basket is empty"
          hint="The oven is full and waiting — go pick something warm."
        >
          <Link to="/products" className="btn btn-primary">
            Explore Our Bakes
          </Link>
        </EmptyState>
      </div>
    );
  }

  // A tapped voucher changes the estimate right here — the same cents-at-a-
  // time math the server will charge. The code only counts once it's on the
  // live board: if the baker pauses it, this tally quietly stops promising it.
  const appliedVoucher = vouchers.find((v) => v.code === appliedCode);
  const { discount, tax, shipping, total } = computeTotals(
    subtotal,
    appliedVoucher?.percent_off ?? 0,
  );
  const remaining = Math.max(0, FREE_SHIPPING_THRESHOLD - subtotal);
  const progress = Math.min(100, (subtotal / FREE_SHIPPING_THRESHOLD) * 100);

  const inCart = new Set(items.map((i) => i.product_id));
  const picks = bestRated
    .filter((p) => p.is_available && p.stock > 0 && !inCart.has(p.id))
    .slice(0, 3);

  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      <h1 className="font-display text-4xl font-semibold">Your basket</h1>
      <p className="mt-1 opacity-70">
        {count} {count === 1 ? "item" : "items"}
      </p>

      <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_380px]">
        {/* Items */}
        <div className="space-y-4">
          {items.map((item) => (
            <div
              key={item.product_id}
              className="flex items-center gap-4 rounded-box glass p-4 shadow-sm ring-1 ring-base-300/60"
            >
              <Link
                to={`/products/${item.slug}`}
                className="h-20 w-20 shrink-0 overflow-hidden rounded-xl shadow-inner"
                aria-label={item.name}
              >
                <PhotoTile
                  src={item.image_url}
                  name={item.name}
                  className="h-full w-full"
                />
              </Link>
              <div className="min-w-0 flex-1">
                <Link
                  to={`/products/${item.slug}`}
                  className="font-display font-semibold hover:text-secondary"
                >
                  {item.name}
                </Link>
                <p className="text-sm opacity-60">
                  {money(item.unit_price)} each
                </p>
                {item.stock <= 5 && (
                  <p className="text-xs font-semibold text-secondary">
                    Only {item.stock} in stock
                  </p>
                )}
              </div>
              <QuantityInput
                value={item.quantity}
                onChange={(q) => setQuantity(item.product_id, q)}
                max={item.stock || 99}
              />
              <p className="w-20 text-right font-bold">
                {money(item.unit_price * item.quantity)}
              </p>
              <button
                type="button"
                className="btn btn-ghost btn-xs text-error"
                onClick={() => remove(item.product_id)}
              >
                Remove
              </button>
            </div>
          ))}

          <Link
            to="/products"
            className="link link-secondary text-sm font-semibold"
          >
            ← Keep browsing
          </Link>

          {picks.length > 0 && (
            <div className="pt-6">
              <div className="rounded-box glass p-5 shadow-sm ring-1 ring-base-300/60">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 className="font-display text-lg font-semibold">
                    Complete the box
                  </h2>
                </div>
                <div className="mt-4 grid gap-4 sm:grid-cols-3">
                  {picks.map((p) => (
                    <div key={p.id} className="flex items-center gap-3">
                      <Link
                        to={`/products/${p.slug}`}
                        className="h-12 w-12 shrink-0 overflow-hidden rounded-lg shadow-inner"
                        aria-label={p.name}
                      >
                        <PhotoTile
                          src={p.image_url}
                          name={p.name}
                          className="h-full w-full"
                        />
                      </Link>
                      <div className="min-w-0 flex-1">
                        <Link
                          to={`/products/${p.slug}`}
                          className="block truncate text-sm font-semibold hover:text-secondary"
                        >
                          {p.name}
                        </Link>
                        <PriceTag product={p} />
                      </div>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm btn-square border border-base-300"
                        onClick={() => {
                          add(p, 1);
                          toast.success(`${p.name} added to your basket`);
                        }}
                        aria-label={`Add ${p.name} to your basket`}
                      >
                        <Plus size={16} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Summary */}
        <aside className="h-fit rounded-box bg-base-200/70 p-6 shadow-sm lg:sticky lg:top-24">
          <h2 className="font-display text-xl font-semibold">The tally</h2>

          <div className="mt-4">
            {remaining > 0 ? (
              <p className="flex items-start gap-2 text-sm">
                <Truck size={18} className="mt-0.5 shrink-0 text-secondary" />
                <span>
                  Add{" "}
                  <span className="font-bold text-secondary">
                    {money(remaining)}
                  </span>{" "}
                  more for free delivery
                </span>
              </p>
            ) : (
              <p className="flex items-center gap-2 text-sm font-semibold text-success">
                <Truck size={16} className="shrink-0" />
                You&rsquo;ve unlocked free delivery
              </p>
            )}
            <progress
              className="progress progress-secondary mt-2 h-2"
              value={progress}
              max={100}
            />
          </div>

          <div className="dash-divider mt-5 space-y-2 pt-5 text-sm">
            <div className="flex justify-between">
              <span className="opacity-70">Subtotal</span>
              <span>{money(subtotal)}</span>
            </div>
            {appliedVoucher && (
              <div className="flex justify-between font-semibold text-success">
                <span>
                  {appliedVoucher.code} ({appliedVoucher.percent_off}% off)
                </span>
                <span>−{money(discount)}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="opacity-70">{taxLabel()}</span>
              <span>{money(tax)}</span>
            </div>
            <div className="flex justify-between">
              <span className="opacity-70">Delivery</span>
              <span>
                {shipping === 0 ? (
                  <span className="font-semibold text-success">FREE</span>
                ) : (
                  money(shipping)
                )}
              </span>
            </div>
            <div className="dash-divider flex justify-between pt-3 text-lg font-bold">
              <span>Estimated total</span>
              <span>{money(total)}</span>
            </div>
          </div>

          {/* The voucher board — every code the shop is offering, and what
              this basket still has to do to earn it */}
          {vouchers.length > 0 && (
            <div className="dash-divider mt-5 pt-5">
              <p className="font-display mb-3 font-semibold">
                Vouchers you can use
              </p>
              <div className="space-y-2.5">
                {vouchers.map((v) => (
                  <VoucherCard
                    key={v.code}
                    voucher={v}
                    subtotal={subtotal}
                    applied={appliedCode === v.code}
                    onApply={applyVoucher}
                    onRemove={removeVoucher}
                  />
                ))}
              </div>
            </div>
          )}

          <Link
            to={user ? "/checkout" : "/login?from=/checkout"}
            className="btn btn-primary btn-lg mt-6 w-full"
          >
            {user ? "Checkout" : "Log in to checkout"}
          </Link>
          <p className="mt-3 text-center text-xs opacity-60">
            Totals confirmed at checkout
          </p>
        </aside>
      </div>
    </div>
  );
}
