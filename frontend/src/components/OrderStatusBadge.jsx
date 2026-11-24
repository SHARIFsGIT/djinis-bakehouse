import { Bike, CheckCheck, CircleCheck, CircleX, Flame, ReceiptText, ShoppingBasket } from "lucide-react";
import { statusGroup, statusLabel } from "../lib/format";

/** The little pictogram for each stop on an order's way through the shop —
 *  beside the word on its badge, and beside the time in the customer's bake
 *  log. */
export const STATUS_ICONS = {
  pending: ReceiptText,
  confirmed: CircleCheck,
  baking: Flame,
  ready: ShoppingBasket,
  out_for_delivery: Bike,
  delivered: CheckCheck,
  cancelled: CircleX,
};

/** An order's status as a badge: its word and its pictogram, coloured by the
 *  group it is in (index.css, "Order statuses"), so a status is never told
 *  by colour alone. `className` carries the size and any layout. */
export default function OrderStatusBadge({ status, className = "" }) {
  const Icon = STATUS_ICONS[status];
  return (
    <span
      className={`order-status badge ${className}`}
      data-status={status}
      data-group={statusGroup(status) ?? undefined}
    >
      {Icon && <Icon size="1em" className="shrink-0" aria-hidden="true" />}
      {statusLabel(status)}
    </span>
  );
}
