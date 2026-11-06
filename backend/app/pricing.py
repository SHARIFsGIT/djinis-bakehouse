"""The shop's one financial calculation service.

Both pricing modes live here, and both the quote endpoint and order
placement price a basket through this module — never their own arithmetic —
so what the storefront displays, what the invoice says, what Stripe is
asked to charge and what the order book stores cannot disagree.

Modes (settings.tax_mode):

* ``exclusive`` — the shipped behaviour, moved here verbatim: net shelf
  prices, the approved flat register rate added on the discounted
  subtotal, delivery added after. Floats + ``round(x, 2)`` exactly as
  before; every existing cent-pinning test must keep passing unchanged.
* ``inclusive`` — the German B2C target: shelf prices ARE gross. Every
  line is priced through app/vat.py (``price_line_inclusive`` — the
  product must carry an approved classification or the basket is refused,
  fail-closed, naming the product), the coupon takes its percent off each
  line's gross, VAT is extracted per discounted line, and the delivery
  charge is split across the basket's rates by ``allocate_shipping``.
  All-Decimal, half-up, cent-exact: the rate rows reconcile to the total
  by construction, and the same rows are what the order snapshots persist.

Delivery pricing itself (the fee, the threshold, the VIP line) is a shop
rule, decided by the caller (routers/orders) and handed in as
``shipping_gross`` — 0.0 means free delivery, and free delivery allocates
no VAT.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from . import vat
from .config import get_settings


class ProductNotVatReady(Exception):
    """Inclusive mode cannot price this basket: a product in it has no
    approved VAT classification. Fail-closed — the caller refuses the
    whole basket, naming the product, rather than price a guess."""

    def __init__(self, product_name: str, reason: str):
        self.product_name = product_name
        self.reason = reason
        super().__init__(f"{product_name}: {reason}")


class BasketTotals:
    """The five figures every priced basket carries (exclusive mode's
    shape) plus the shelf subtotal both modes report the same way."""

    def __init__(self, subtotal, discount, taxable, tax, shipping, total):
        self.subtotal = subtotal
        self.discount = discount
        self.taxable = taxable
        self.tax = tax
        self.shipping = shipping
        self.total = total


# --------------------------------------------------------------------------
# Exclusive mode — the historical recipe, byte-for-byte
# --------------------------------------------------------------------------

def price_basket_exclusive(subtotal: float, coupon, free_threshold: float, tax_rate: float, shipping_fee: float) -> BasketTotals:
    """Net shelf prices + the flat register rate on the discounted
    subtotal, delivery judged on the pre-discount shelf value. The exact
    arithmetic routers/orders._price_basket has always run — moved, not
    changed; the suite pins it to the cent."""
    discount = round(subtotal * coupon.percent_off / 100, 2) if coupon else 0.0
    taxable = round(subtotal - discount, 2)
    tax = round(taxable * tax_rate, 2)
    shipping = 0.0 if subtotal >= free_threshold else shipping_fee
    total = round(taxable + tax + shipping, 2)
    return BasketTotals(
        subtotal=subtotal, discount=discount, taxable=taxable, tax=tax, shipping=shipping, total=total
    )


# --------------------------------------------------------------------------
# Inclusive mode — gross shelf prices, per-line VAT, allocated shipping
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PricedLine:
    """One basket line's complete, immutable money picture."""

    product_id: int
    name: str
    quantity: int
    vat_category: str | None
    vat_rate: Decimal
    # Unit figures mirror the shelf price (gross unit == the catalog price);
    # line figures carry the coupon's slice.
    net_unit_price: Decimal
    gross_unit_price: Decimal
    gross_line_total: Decimal          # before the coupon
    discount_amount: Decimal           # this line's slice of the coupon
    gross_after_discount: Decimal
    net_line_total: Decimal
    vat_amount: Decimal


@dataclass(frozen=True)
class RateRow:
    """One VAT rate's share of the whole order — products plus the
    delivery allocated to that rate. This is the OrderTaxLine shape."""

    vat_rate: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal
    shipping_gross: Decimal = Decimal("0.00")  # the delivery slice inside gross

    def plus_shipping(self, ship) -> "RateRow":
        return RateRow(
            vat_rate=self.vat_rate,
            net_amount=self.net_amount + ship.net_amount,
            vat_amount=self.vat_amount + ship.vat_amount,
            gross_amount=self.gross_amount + ship.gross_amount,
            shipping_gross=ship.gross_amount,
        )


@dataclass(frozen=True)
class InclusiveBasket:
    """The complete priced basket — totals, per-line snapshots and per-rate
    rows, all Decimal cents, all reconciling to `total_payable`."""

    lines: list
    subtotal_gross: Decimal            # shelf value of the basket
    discount_total: Decimal
    product_net: Decimal
    product_vat: Decimal
    shipping_gross: Decimal
    shipping_net: Decimal
    shipping_vat: Decimal
    vat_total: Decimal                 # products + delivery
    total_payable: Decimal
    rate_rows: list = field(default_factory=list)
    tax_included: bool = True

    # The same field names the exclusive result carries, as floats — the
    # routers and the Order row treat both modes uniformly.
    @property
    def subtotal(self) -> float:
        return float(self.subtotal_gross)

    @property
    def discount(self) -> float:
        return float(self.discount_total)

    @property
    def tax(self) -> float:
        return float(self.vat_total)

    @property
    def shipping(self) -> float:
        return float(self.shipping_gross)

    @property
    def total(self) -> float:
        return float(self.total_payable)


def price_basket_inclusive(db, merged: dict, coupon, shipping_gross) -> InclusiveBasket:
    """Price a basket of {product: quantity} at gross shelf prices.

    Raises ProductNotVatReady for the first product that cannot be priced
    (unclassified, parked for review, or not approved) — the caller turns
    that into a fail-closed refusal naming the product. The coupon takes
    its whole percent off every line's gross (a discount reduces the VAT
    base with it), each discounted line is re-extracted at its own rate,
    and the delivery charge is allocated across the discounted lines' rate
    buckets. Every figure below is Decimal cents, half-up.
    """
    percent = Decimal(str(coupon.percent_off)) if coupon else Decimal("0")

    lines = []
    for product, quantity in merged.items():
        try:
            base = vat.price_line_inclusive(product, quantity, db)
        except vat.VatError as exc:
            raise ProductNotVatReady(product.name, str(exc)) from None
        discount = (
            vat.round_cents(base.gross_line_total * percent / Decimal("100"))
            if coupon
            else Decimal("0.00")
        )
        gross_after = base.gross_line_total - discount
        net_after, vat_after = vat.extract_vat(gross_after, base.vat_rate)
        lines.append(
            PricedLine(
                product_id=product.id,
                name=product.name,
                quantity=quantity,
                vat_category=base.vat_category,
                vat_rate=base.vat_rate,
                net_unit_price=base.net_unit_price,
                gross_unit_price=base.gross_unit_price,
                gross_line_total=base.gross_line_total,
                discount_amount=discount,
                gross_after_discount=gross_after,
                net_line_total=net_after,
                vat_amount=vat_after,
            )
        )

    # Product buckets per rate, from the discounted lines.
    buckets: dict[Decimal, list[Decimal]] = {}
    for line in lines:
        b = buckets.setdefault(line.vat_rate, [Decimal("0.00")] * 3)
        b[0] += line.net_line_total
        b[1] += line.vat_amount
        b[2] += line.gross_after_discount
    summaries = [
        vat.RateSummary(vat_rate=rate, net_amount=v[0], vat_amount=v[1], gross_amount=v[2])
        for rate, v in sorted(buckets.items())
    ]

    # A fully-discounted basket has no taxable value left to allocate by —
    # but the delivery still follows the products it delivers, so the basis
    # becomes their pre-discount values rather than a single highest rate.
    if sum((s.net_amount for s in summaries), Decimal("0.00")) == 0 and any(
        line.gross_line_total > 0 for line in lines
    ):
        pre: dict[Decimal, list[Decimal]] = {}
        for line in lines:
            if line.gross_line_total <= 0:
                continue
            net_pre, _ = vat.extract_vat(line.gross_line_total, line.vat_rate)
            b = pre.setdefault(line.vat_rate, [Decimal("0.00")] * 3)
            b[0] += net_pre
            b[2] += line.gross_line_total
        summaries = [
            vat.RateSummary(vat_rate=rate, net_amount=v[0], vat_amount=Decimal("0.00"), gross_amount=v[2])
            for rate, v in sorted(pre.items())
        ]

    shipping = vat.round_cents(shipping_gross)
    ship_rows = vat.allocate_shipping(shipping, summaries) if shipping > 0 else []

    product_net = sum((line.net_line_total for line in lines), Decimal("0.00"))
    product_vat = sum((line.vat_amount for line in lines), Decimal("0.00"))
    subtotal_gross = sum((line.gross_line_total for line in lines), Decimal("0.00"))
    discount_total = sum((line.discount_amount for line in lines), Decimal("0.00"))
    shipping_net = sum((r.net_amount for r in ship_rows), Decimal("0.00"))
    shipping_vat = sum((r.vat_amount for r in ship_rows), Decimal("0.00"))

    by_rate = {s.vat_rate: RateRow(s.vat_rate, s.net_amount, s.vat_amount, s.gross_amount) for s in summaries}
    for row in ship_rows:
        if row.vat_rate in by_rate:
            by_rate[row.vat_rate] = by_rate[row.vat_rate].plus_shipping(row)
        else:  # a bucket too small for a product cent can still carry shipping
            by_rate[row.vat_rate] = RateRow(row.vat_rate, row.net_amount, row.vat_amount, row.gross_amount, row.gross_amount)

    return InclusiveBasket(
        lines=lines,
        subtotal_gross=subtotal_gross,
        discount_total=discount_total,
        product_net=product_net,
        product_vat=product_vat,
        shipping_gross=shipping,
        shipping_net=shipping_net,
        shipping_vat=shipping_vat,
        vat_total=product_vat + shipping_vat,
        total_payable=subtotal_gross - discount_total + shipping,
        rate_rows=[by_rate[r] for r in sorted(by_rate)],
    )


def price_basket(db, merged, coupon, free_threshold: float, *, tax_rate, shipping_fee: float):
    """The one entry point the routers call. Dispatches on the configured
    pricing mode and never lets the two recipes mix."""
    if get_settings().tax_mode == "inclusive":
        shipping = 0.0 if _shelf_subtotal(merged) >= free_threshold else shipping_fee
        return price_basket_inclusive(db, merged, coupon, shipping)
    return price_basket_exclusive(
        _shelf_subtotal(merged), coupon, free_threshold, tax_rate, shipping_fee
    )


def _shelf_subtotal(merged) -> float:
    """The basket's shelf value — the pre-discount product subtotal both
    modes judge the free-delivery threshold on, rounded as the catalog
    prices are."""
    return round(sum(p.current_price * q for p, q in merged.items()), 2)
