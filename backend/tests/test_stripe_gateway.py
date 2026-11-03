"""The real gateway's checkout-page builder, proven without the wire.

test_api.py swaps the whole stripe_gateway module for a fake so the API
suite never talks to Stripe; this file loads a private second copy of the
module (as part of the app package, so its relative config import works)
and runs the real create_checkout_session against a stub SDK. What matters
here is the hosted page's shape: itemised lines that sum to the invoice to
the cent, the coupon as a real discount, the bakery branding, the copy
slots — and that nothing about a photo can break a payment.
"""

import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "stripe_gateway.py"
BASE_URL = "https://shop.test"


class StubStripe:
    """Records every gateway call; no request leaves the process."""

    api_key = None
    raise_on_create = False

    def __init__(self):
        self.coupons: list[dict] = []
        self.sessions: list[dict] = []
        self.checkout = SimpleNamespace(Session=SimpleNamespace(create=self._session))
        self.Coupon = SimpleNamespace(create=self._coupon)

    def _coupon(self, **kwargs):
        self.coupons.append(kwargs)
        return SimpleNamespace(id=f"coupon_{len(self.coupons)}")

    def _session(self, **kwargs):
        if self.raise_on_create:
            raise RuntimeError("stripe down")
        self.sessions.append(kwargs)
        return SimpleNamespace(id="cs_test_page", url="https://checkout.stripe.test/cs_test_page")


def load_real_gateway(stub: StubStripe):
    spec = importlib.util.spec_from_file_location("app._stripe_gateway_real", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.stripe = stub
    return module


def make_order(
    *,
    unit_price=3.5,
    quantity=4,
    discount=0.0,
    coupon_code=None,
    shipping=5.99,
    tax=1.23,
    photo="/static/products/croissant.jpg",
):
    """An order shaped like the API's own output (croissants by default),
    totals pre-computed with the shop's money math."""
    product = SimpleNamespace(image_url=photo) if photo else None
    item = SimpleNamespace(
        product_name="Butter Croissant",
        unit_price=unit_price,
        quantity=quantity,
        subtotal=round(unit_price * quantity, 2),
        product=product,
    )
    subtotal = round(unit_price * quantity, 2)
    taxable = round(subtotal - discount, 2)
    return SimpleNamespace(
        id=7,
        order_number="DB-TEST01",
        items=[item],
        subtotal=subtotal,
        discount_amount=discount,
        coupon_code=coupon_code,
        tax=tax,
        shipping_fee=shipping,
        total=round(taxable + tax + shipping, 2),
        user=SimpleNamespace(email="taster@example.com"),
    )


def line_total_cents(session_kwargs) -> int:
    """Σ unit_amount × quantity across every line, in integer cents."""
    return sum(
        line["price_data"]["unit_amount"] * line["quantity"]
        for line in session_kwargs["line_items"]
    )


def test_lines_sum_to_the_invoice_and_the_page_is_dressed() -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    order = make_order()  # 14.00 + 1.23 tax + 5.99 delivery = 21.22

    result = gateway.create_checkout_session(order, BASE_URL)

    assert result == {"id": "cs_test_page", "url": "https://checkout.stripe.test/cs_test_page"}
    assert len(stub.sessions) == 1
    kwargs = stub.sessions[0]

    # The page can never disagree with the invoice: lines are the stored
    # subtotal + tax + delivery, in integer cents.
    assert line_total_cents(kwargs) == gateway.cents(float(order.total)) == 2122

    # One line per product, with its photo as an absolute URL.
    croissant = kwargs["line_items"][0]
    assert croissant["price_data"]["product_data"]["name"] == "Butter Croissant"
    assert croissant["price_data"]["unit_amount"] == 350
    assert croissant["quantity"] == 4
    assert croissant["price_data"]["product_data"]["images"] == [
        f"{BASE_URL}/static/products/croissant.jpg"
    ]
    # Then delivery and tax rows, named like the storefront's checkout.
    names = [line["price_data"]["product_data"]["name"] for line in kwargs["line_items"]]
    assert names == ["Butter Croissant", "Delivery", "Tax (8.75%)"]
    delivery = kwargs["line_items"][1]
    assert delivery["price_data"]["unit_amount"] == 599

    # The bakery's palette, a serif face, and the two copy slots.
    assert kwargs["branding_settings"] == {
        "background_color": "#f6f1ee",
        "button_color": "#7f2230",
        "border_style": "rounded",
        "font_family": "lora",
        "display_name": "Djini's Bakehouse",
    }
    assert kwargs["custom_text"]["submit"]["message"]
    assert kwargs["custom_text"]["after_submit"]["message"]

    # Reconciliation and routing, unchanged from the simple era.
    assert kwargs["client_reference_id"] == "DB-TEST01"
    assert kwargs["customer_email"] == "taster@example.com"
    assert kwargs["metadata"] == {"order_id": "7"}
    assert kwargs["payment_intent_data"]["description"] == "Order DB-TEST01"
    assert kwargs["success_url"] == f"{BASE_URL}/account/orders/7?placed=1&stripe=return"
    assert kwargs["cancel_url"] == f"{BASE_URL}/account/orders/7?stripe=cancelled"
    assert 0 < kwargs["expires_at"] - int(time.time()) <= gateway.SESSION_LIFETIME_SECONDS


def test_coupon_discount_rides_as_a_real_stripe_discount() -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)
    # A 60.00 basket — free delivery territory — with 10% off: 60.00 − 6.00,
    # tax on 54.00, total 58.73.
    order = make_order(
        unit_price=15.0, discount=6.0, coupon_code="WELCOME10", shipping=0.0, tax=4.73
    )

    gateway.create_checkout_session(order, BASE_URL)

    kwargs = stub.sessions[0]
    # Delivery shows even when free — a zero line renders as free.
    delivery = kwargs["line_items"][1]
    assert delivery["price_data"]["unit_amount"] == 0
    # One one-off coupon for exactly the stored discount, named with the code.
    assert stub.coupons == [
        {
            "amount_off": 600,
            "currency": kwargs["line_items"][0]["price_data"]["currency"],
            "duration": "once",
            "name": "WELCOME10",
            "metadata": {"order_number": "DB-TEST01"},
        }
    ]
    assert kwargs["discounts"] == [{"coupon": "coupon_1"}]
    # Lines minus the discount still equal the invoice: 6473 − 600 = 58.73.
    assert line_total_cents(kwargs) == 6473
    assert line_total_cents(kwargs) - 600 == gateway.cents(float(order.total)) == 5873


def test_no_coupon_no_discounts_key() -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    gateway.create_checkout_session(make_order(), BASE_URL)

    assert stub.coupons == []
    assert "discounts" not in stub.sessions[0]


def test_photo_troubles_never_break_payment() -> None:
    stub = StubStripe()
    gateway = load_real_gateway(stub)

    # No product behind the line (deleted since), no photo at all, an
    # already-absolute URL, and a lookup that explodes — all fine, all
    # session-creating, only the thumbnail changes.
    for photo_setup in (
        SimpleNamespace(product=None, image_url=None),  # product gone
        SimpleNamespace(product=SimpleNamespace(image_url=None)),  # no photo
        SimpleNamespace(product=SimpleNamespace(image_url="https://cdn.test/c.jpg")),
    ):
        order = make_order()
        order.items[0].product = photo_setup
        gateway.create_checkout_session(order, BASE_URL)

    class Exploding:
        @property
        def product(self):
            raise RuntimeError("detached instance")

    order = make_order()
    order.items[0] = SimpleNamespace(
        product_name="Butter Croissant", unit_price=3.5, quantity=4, product=Exploding()
    )
    result = gateway.create_checkout_session(order, BASE_URL)
    assert result["id"] == "cs_test_page"
    assert "images" not in stub.sessions[-1]["line_items"][0]["price_data"]["product_data"]


def test_a_silent_stripe_becomes_gateway_unavailable() -> None:
    stub = StubStripe()
    stub.raise_on_create = True
    gateway = load_real_gateway(stub)

    with pytest.raises(gateway.GatewayUnavailable):
        gateway.create_checkout_session(make_order(), BASE_URL)
    assert stub.sessions == []
