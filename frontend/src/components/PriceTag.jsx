import { money } from "../lib/format";

export default function PriceTag({ product, large = false }) {
  if (!product.is_on_sale) {
    return <span className={`font-bold ${large ? "text-3xl" : ""}`}>{money(product.current_price)}</span>;
  }
  return (
    <span className="inline-flex items-baseline gap-2">
      <span className={`font-bold text-secondary ${large ? "text-3xl" : ""}`}>
        {money(product.current_price)}
      </span>
      <span className={`line-through opacity-50 ${large ? "text-lg" : "text-xs"}`}>
        {money(product.price)}
      </span>
    </span>
  );
}
