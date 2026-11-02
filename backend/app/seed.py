"""Seed the database on first run (idempotent).

The catalog — categories, products, the two vouchers the storefront copy
promises — always seeds. Demo customers, order history and reviews seed only
outside production (or with SEED_DEMO_DATA=true); a fresh production database
instead gets the real admin from ADMIN_EMAIL / ADMIN_PASSWORD.

Product slugs map 1:1 to photos in backend/static/products/ — see
scripts/prepare_images.py if you ever re-generate the images.
"""

import logging
from datetime import timedelta

from sqlalchemy import func, select

from .config import get_settings
from .database import SessionLocal, utcnow
from .models import (
    Address,
    Category,
    ComingSoonItem,
    Coupon,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    Product,
    Review,
    Testimonial,
    User,
)
from .security import hash_password

logger = logging.getLogger("bakery")

CATEGORIES = [
    ("Breads", "breads", "/static/products/french-baguette.jpg", 1),
    ("Pastries", "pastries", "/static/products/butter-croissant.jpg", 2),
    ("Desserts", "desserts", "/static/products/glazed-doughnuts.jpg", 3),
]

# name, slug, category, price, sale_price, featured, stock, prep_minutes,
# description, ingredients, allergens
PRODUCTS = [
    # --- breads ---------------------------------------------------------
    ("French Baguette", "french-baguette", "breads", 4.50, None, False, 36, 720,
     "A slow-fermented baguette with a crackling crust and an open, chewy crumb. Best the day it's baked.",
     "Wheat flour, water, salt, yeast", "Gluten"),
    ("Fresh Bagels", "fresh-bagels", "breads", 6.50, None, False, 24, 480,
     "Four kettle-boiled bagels with a blistered crust and a dense, chewy middle. Plain or sesame.",
     "Wheat flour, water, malt, salt, yeast, sesame seeds", "Gluten, Sesame"),
    ("Beef Floss Bread", "beef-floss-bread", "breads", 7.50, 5.95, False, 18, 300,
     "Soft milk bread topped with savoury beef floss and a swipe of mayo. A shop regular since day one.",
     "Wheat flour, milk, egg, sugar, butter, beef floss, mayonnaise", "Gluten, Eggs, Dairy, Beef"),
    ("Triple Cheese Bread", "triple-cheese-bread", "breads", 8.50, None, True, 14, 360,
     "Pillowy bread pulled apart into knots and baked under mozzarella, cheddar and parmesan.",
     "Wheat flour, milk, butter, mozzarella, cheddar, parmesan, garlic, herbs", "Gluten, Dairy, Eggs"),
    ("Blueberry Cheese Bread", "blueberry-cheese-bread", "breads", 7.50, 5.95, False, 16, 360,
     "Sweet milk bread swirled with blueberry compote and a cream cheese core.",
     "Wheat flour, milk, cream cheese, blueberries, sugar, butter, egg", "Gluten, Dairy, Eggs"),
    ("Wool Roll Bread", "wool-roll-bread", "breads", 6.50, None, True, 20, 420,
     "A tray of tear-apart rolls brushed with garlic butter — as fun to pull apart as it is to eat.",
     "Wheat flour, milk, butter, sugar, egg, garlic, parsley, salt", "Gluten, Dairy, Eggs"),
    ("Rosemary Focaccia", "rosemary-focaccia", "breads", 7.00, None, False, 12, 1440,
     "Overnight-risen focaccia, dimpled and drenched in olive oil with rosemary and flaky salt.",
     "Wheat flour, olive oil, rosemary, sea salt, yeast", "Gluten"),
    ("Garden Focaccia", "garden-focaccia", "breads", 8.50, None, True, 10, 1440,
     "Our focaccia dressed like a garden — bell peppers, olives, onion and cherry tomatoes pressed into the dough.",
     "Wheat flour, olive oil, bell peppers, cherry tomatoes, olives, red onion, herbs", "Gluten"),
    # --- pastries -------------------------------------------------------
    ("Butter Croissant", "butter-croissant", "pastries", 3.75, None, True, 48, 1440,
     "Laminated over two days with cultured butter. Shatters when you tear it, smells like a Paris morning.",
     "Wheat flour, butter, milk, sugar, yeast, salt", "Gluten, Dairy"),
    ("Croffle", "croffle", "pastries", 5.50, None, True, 22, 240,
     "Croissant dough pressed in a waffle iron — crisp ridges, flaky pockets, served warm with cinnamon sugar.",
     "Wheat flour, butter, milk, sugar, cinnamon, yeast, salt", "Gluten, Dairy"),
    ("Apple Strudel", "apple-strudel", "pastries", 5.25, None, False, 15, 180,
     "Hand-stretched pastry rolled around spiced apple filling with raisins and toasted crumbs.",
     "Wheat flour, butter, apples, raisins, sugar, cinnamon, breadcrumbs", "Gluten, Dairy"),
    ("Beef Curry Puff", "beef-curry-puff", "pastries", 4.75, None, True, 26, 120,
     "Flaky spiral pastry wrapped around a spiced beef curry filling — our most requested savoury bite.",
     "Wheat flour, butter, beef, onion, curry spices, potato", "Gluten, Dairy, Beef"),
    ("Chocolate Cream Puff", "chocolate-cream-puff", "pastries", 4.25, None, False, 30, 90,
     "Choux pastry filled with dark chocolate custard and finished with a cocoa dusting.",
     "Wheat flour, butter, eggs, milk, dark chocolate, cream, sugar", "Gluten, Dairy, Eggs"),
    ("Fruit Cushion Pastry", "fruit-cushion-pastry", "pastries", 5.75, None, False, 12, 120,
     "Puff pastry pillow with fresh seasonal fruit and a vanilla pastry cream base.",
     "Wheat flour, butter, seasonal fruit, cream, milk, sugar, vanilla", "Gluten, Dairy, Eggs"),
    ("Rhum Horn", "rhum-horn", "pastries", 4.95, 3.95, False, 18, 150,
     "A horn of brioche soaked in rum syrup and filled with chantilly cream. Strictly for grown-ups.",
     "Wheat flour, butter, eggs, sugar, rum, cream", "Gluten, Dairy, Eggs, Alcohol"),
    ("Vol-au-Vent", "vol-au-vent", "pastries", 5.25, None, False, 4, 120,
     "A crown of hollow puff pastry filled with chicken and mushroom in cream sauce. Limited batch daily.",
     "Wheat flour, butter, chicken, mushroom, cream, onion, herbs", "Gluten, Dairy"),
    ("Pretzels & Bagels", "pretzels-and-bagels", "pastries", 6.25, 4.95, False, 16, 480,
     "Two soft pretzels and two bagels with coarse salt — the afternoon snacking box.",
     "Wheat flour, water, malt, salt, yeast, baking soda", "Gluten"),
    ("Cheese Straws", "cheese-straws", "pastries", 4.50, None, False, 20, 90,
     "Twisted puff pastry straws baked with aged cheddar and a pinch of cayenne. A dozen per box.",
     "Wheat flour, butter, cheddar, egg, cayenne, salt", "Gluten, Dairy, Eggs"),
    # --- desserts -------------------------------------------------------
    ("Glazed Doughnuts", "glazed-doughnuts", "desserts", 5.95, None, True, 28, 180,
     "Six pillowy doughnuts with a vanilla bean glaze and rainbow sprinkles. Made Friday to Sunday.",
     "Wheat flour, milk, sugar, yeast, butter, glaze, sprinkles", "Gluten, Dairy, Eggs"),
    ("Maritozzo", "maritozzo", "desserts", 6.25, 5.25, False, 16, 120,
     "The Roman street sweet: split brioche loaded with clouds of whipped cream.",
     "Wheat flour, milk, butter, eggs, sugar, cream, vanilla", "Gluten, Dairy, Eggs"),
    ("Strawberry Maritozzo", "strawberry-maritozzo", "desserts", 6.95, None, True, 14, 120,
     "Our maritozzo folded with fresh strawberries and cream — the photo everyone takes home.",
     "Wheat flour, milk, butter, eggs, sugar, cream, strawberries", "Gluten, Dairy, Eggs"),
    ("Strawberry Mille-Feuille", "strawberry-mille-feuille", "desserts", 7.25, None, False, 8, 240,
     "Layers of caramelised puff pastry, vanilla custard and sliced strawberries.",
     "Wheat flour, butter, milk, cream, eggs, sugar, strawberries, vanilla", "Gluten, Dairy, Eggs"),
    ("Strawberry Puff", "strawberry-puff", "desserts", 5.95, None, False, 11, 90,
     "Cream puff topped with a strawberry glaze and a whole berry hidden inside.",
     "Wheat flour, butter, eggs, milk, cream, strawberries, sugar", "Gluten, Dairy, Eggs"),
    ("Double Chocolate Cream Bun", "double-chocolate-cream-bun", "desserts", 5.50, None, False, 19, 120,
     "Cocoa brioche, chocolate custard filling, chocolate glaze. No restraint intended.",
     "Wheat flour, cocoa, milk, butter, eggs, dark chocolate, cream, sugar", "Gluten, Dairy, Eggs"),
    ("Christmas Stollen", "christmas-stollen", "desserts", 12.00, 9.95, False, 0, 2880,
     "Seasonal — returns in December. Dense fruit and marzipan loaf dusted with snow sugar.",
     "Wheat flour, butter, raisins, candied peel, marzipan, rum, spices", "Gluten, Dairy, Alcohol"),
]

# code, percent, description, min_subtotal (None = no minimum)
COUPONS = [
    ("WELCOME10", 10, "10% off your first order", None),
    ("FRESHBAKE15", 15, "15% off boxes of $40 or more", 40.00),
]

# The home page's "Word around the neighborhood" — the baker's picks, not an
# automatic feed; she can rewrite all of these from Settings.
TESTIMONIALS = [
    # name, role, quote, avatar, rating, order
    ("Tanvir Hasan", "Saturday regular",
     "I get two croissants every Saturday morning and they're still warm by the time I'm home. The croffle is worth crossing town for.",
     "/static/site/avatar-tanvir.jpg", 5, 1),
    ("Mustafa Alam", "Chef, 30 years in hotel kitchens",
     "I've worked pastry stations on three continents. Djini's laminated dough is the real thing — proper butter, proper patience.",
     "/static/site/avatar-rahman.jpg", 5, 2),
    ("Farida Islam", "Orders for the whole office",
     "The box for our Friday stand-up arrives on time, every time, and nothing survives the meeting. Nothing.",
     None, 4, 3),
]

# The home page's look-ahead band — what's coming to the counter soon.
COMING_SOON = [
    # title, description, image, eta label, order
    ("Bespoke celebration cakes",
     "Two-tier celebration cakes built to order — choose the sponge, the filling and the finish. Minimum three days' notice.",
     "/static/products/strawberry-maritozzo.jpg", "Taking orders soon", 1),
    ("Sourdough pizza nights",
     "Weekend evenings only: blistered sourdough bases, seasonal toppings, baked in ninety seconds. Eat in or take away.",
     "/static/products/french-baguette.jpg", "Fridays from this winter", 2),
    ("Stollen season",
     "The Christmas stollen returns — dense fruit and marzipan, dusted like snow. Reserve yours before they're gone.",
     "/static/products/christmas-stollen.jpg", "From December", 3),
]

DEMO_USERS = [
    # email, password, first, last, role
    ("admin@djinisbakehouse.com", "Admin123!", "Djini", "Rahman", "admin"),
    ("customer@example.com", "Customer123!", "Amara", "Chowdhury", "customer"),
    ("tanvir@example.com", "Customer123!", "Tanvir", "Hasan", "customer"),
    ("mustafa@example.com", "Customer123!", "Mustafa", "Alam", "customer"),
    ("farida@example.com", "Customer123!", "Farida", "Islam", "customer"),
]

REVIEWS = [
    # product slug, reviewer email, rating, days_ago, comment
    ("butter-croissant", "tanvir@example.com", 5, 21, "I get two every Saturday morning. Still warm by the time I'm home."),
    ("butter-croissant", "mustafa@example.com", 5, 14, "Thirty years in hotel kitchens and this is the best lamination I've had outside of France."),
    ("butter-croissant", "farida@example.com", 4, 9, "Beautiful crust. Slightly smaller than last month but just as good."),
    ("croffle", "tanvir@example.com", 5, 11, "Ordered on a whim and now my kids ask for it every week."),
    ("croffle", "farida@example.com", 4, 3, "Crisp outside, soft inside. Ask for it warmed up."),
    ("beef-curry-puff", "mustafa@example.com", 5, 17, "The spice level is perfect. Got six for a meeting and they vanished."),
    ("triple-cheese-bread", "farida@example.com", 5, 8, "Cheese pull for days. Reheats well in the oven too."),
    ("triple-cheese-bread", "tanvir@example.com", 5, 2, "Dangerous. I ate half the tray before I got home."),
    ("glazed-doughnuts", "tanvir@example.com", 5, 6, "Fresh on Sundays if you come before ten. The glaze ratio is right."),
    ("glazed-doughnuts", "farida@example.com", 3, 4, "Good but a touch doughy in the middle for me."),
    ("strawberry-maritozzo", "farida@example.com", 5, 5, "Gorgeous and not too sweet. The cream is properly whipped, not canned."),
    ("strawberry-maritozzo", "tanvir@example.com", 5, 1, "Bought it for a photo, stayed for the taste."),
    ("garden-focaccia", "mustafa@example.com", 5, 13, "The garden one is almost too pretty to cut. The olive oil is excellent quality."),
    ("french-baguette", "mustafa@example.com", 4, 19, "Proper crust. Toast day-old slices for bruschetta."),
    ("maritozzo", "farida@example.com", 4, 7, "Rich. One is plenty, and I mean that as a compliment."),
    ("wool-roll-bread", "tanvir@example.com", 5, 10, "The garlic butter ones go fast on weekends. Worth the early trip."),
    ("cheese-straws", "farida@example.com", 4, 12, "Salty, flaky, gone in one sitting."),
    ("rhum-horn", "mustafa@example.com", 5, 16, "Real rum, real cream. A proper adult pastry."),
]

# user email, days_ago, status, payment, [(slug, qty), ...], coupon, hour
ORDER_HISTORY = [
    ("tanvir@example.com", 12, "delivered", "card", [("butter-croissant", 4), ("beef-curry-puff", 6)], None, 10),
    ("farida@example.com", 10, "delivered", "card", [("triple-cheese-bread", 2), ("glazed-doughnuts", 2), ("cheese-straws", 1)], "WELCOME10", 9),
    ("customer@example.com", 8, "delivered", "cash_on_delivery", [("wool-roll-bread", 1), ("strawberry-maritozzo", 2)], None, 14),
    ("mustafa@example.com", 6, "delivered", "card", [("garden-focaccia", 2), ("french-baguette", 3), ("rhum-horn", 4)], None, 16),
    ("tanvir@example.com", 4, "out_for_delivery", "card", [("croffle", 3), ("strawberry-puff", 2)], None, 8),
    ("customer@example.com", 3, "baking", "card", [("strawberry-maritozzo", 2), ("blueberry-cheese-bread", 1), ("fresh-bagels", 1)], "FRESHBAKE15", 11),
    ("farida@example.com", 2, "confirmed", "cash_on_delivery", [("chocolate-cream-puff", 4), ("maritozzo", 2)], None, 13),
    ("mustafa@example.com", 1, "cancelled", "card", [("christmas-stollen", 2)], None, 15),
]

STATUSES_AFTER = {
    OrderStatus.CONFIRMED: [OrderStatus.CONFIRMED],
    OrderStatus.BAKING: [OrderStatus.CONFIRMED, OrderStatus.BAKING],
    OrderStatus.READY: [OrderStatus.CONFIRMED, OrderStatus.BAKING, OrderStatus.READY],
    OrderStatus.OUT_FOR_DELIVERY: [OrderStatus.CONFIRMED, OrderStatus.BAKING, OrderStatus.READY, OrderStatus.OUT_FOR_DELIVERY],
    OrderStatus.DELIVERED: [OrderStatus.CONFIRMED, OrderStatus.BAKING, OrderStatus.READY, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED],
    OrderStatus.CANCELLED: [OrderStatus.CONFIRMED, OrderStatus.CANCELLED],
}

EVENT_COMMENTS = {
    OrderStatus.CONFIRMED: "Order confirmed — see you on delivery day.",
    OrderStatus.BAKING: "Your bake is in the oven.",
    OrderStatus.READY: "Baked, boxed and ready to go.",
    OrderStatus.OUT_FOR_DELIVERY: "Out for delivery.",
    OrderStatus.DELIVERED: "Delivered. Enjoy!",
    OrderStatus.CANCELLED: "Cancelled by customer. Payment refunded.",
}

DEMO_ADDRESSES = {
    "customer@example.com": ("Home", "Amara Chowdhury", "+1 (503) 555-0164",
                             "2116 SE Division St", "Apt 4", "Portland", "OR", "97202"),
    "tanvir@example.com": ("Home", "Tanvir Hasan", "+1 (503) 555-0113",
                           "88 N Russell St", None, "Portland", "OR", "97227"),
    "mustafa@example.com": ("Home", "Mustafa Alam", "+1 (971) 555-0198",
                            "4415 NE Fremont St", None, "Portland", "OR", "97213"),
    "farida@example.com": ("Work", "Farida Islam", "+1 (503) 555-0151",
                           "1201 SW Morrison St", "Suite 300", "Portland", "OR", "97205"),
}


def _seed_order(db, users: dict, products: dict, coupons: dict, index: int, seq: tuple) -> None:
    """Create one historical order with a consistent timeline and totals."""
    settings = get_settings()
    email, days_ago, status, payment, items, coupon_code, hour = seq
    final_status = OrderStatus(status)
    payment_method = PaymentMethod(payment)
    placed_at = utcnow().replace(hour=hour, minute=24, second=0, microsecond=0) - timedelta(days=days_ago)

    subtotal = 0.0
    order_items = []
    for slug, qty in items:
        product = products[slug]
        unit_price = product.current_price
        line = round(unit_price * qty, 2)
        subtotal = round(subtotal + line, 2)
        order_items.append((product, unit_price, qty, line))

    coupon = coupons.get(coupon_code) if coupon_code else None
    discount = round(subtotal * coupon.percent_off / 100, 2) if coupon else 0.0
    taxable = round(subtotal - discount, 2)
    tax = round(taxable * settings.tax_rate, 2)
    shipping = 0.0 if subtotal >= settings.free_shipping_threshold else settings.shipping_fee
    total = round(taxable + tax + shipping, 2)

    _label, recipient, phone, line1, line2, city, state, postal = DEMO_ADDRESSES[email]
    payment_status = PaymentStatus.PAID if payment_method == PaymentMethod.CARD else PaymentStatus.PENDING
    if final_status == OrderStatus.CANCELLED and payment_method == PaymentMethod.CARD:
        payment_status = PaymentStatus.REFUNDED
    if final_status == OrderStatus.DELIVERED:
        payment_status = PaymentStatus.PAID

    order = Order(
        order_number=f"DJ-{placed_at:%Y%m%d}-{1001 + index}",
        user_id=users[email].id,
        status=final_status.value,
        payment_method=payment_method.value,
        payment_status=payment_status.value,
        subtotal=subtotal,
        discount_amount=discount,
        coupon_code=coupon_code,
        tax=tax,
        shipping_fee=shipping,
        total=total,
        delivery_date=(placed_at + timedelta(days=2)).date(),
        recipient=recipient,
        phone=phone,
        line1=line1,
        line2=line2,
        city=city,
        state=state,
        postal_code=postal,
        country="USA",
        created_at=placed_at,
        updated_at=placed_at,
    )
    db.add(order)
    db.flush()

    for product, unit_price, qty, line in order_items:
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=product.name,
                product_slug=product.slug,
                unit_price=unit_price,
                quantity=qty,
                subtotal=line,
            )
        )

    db.add(OrderEvent(order_id=order.id, status=OrderStatus.PENDING.value,
                      comment=None, created_at=placed_at))
    for i, event_status in enumerate(STATUSES_AFTER[final_status], start=1):
        db.add(
            OrderEvent(
                order_id=order.id,
                status=event_status.value,
                comment=EVENT_COMMENTS[event_status],
                created_at=placed_at + timedelta(hours=2 * i + 1),
            )
        )


def _restore_promised_content(db) -> None:
    """Keep an already-seeded database in step with what the storefront
    promises: the coupon codes (with their spend thresholds) always, and the
    demo testimonials / coming-soon bands only while demo data is wanted.
    Only fills genuine gaps — anything the baker has deliberately deleted or
    retuned from the console is left alone (a coupon threshold counts as
    untouched only while it is still NULL)."""
    existing_coupons = {c.code: c for c in db.scalars(select(Coupon))}
    for code, percent_off, description, min_subtotal in COUPONS:
        coupon = existing_coupons.get(code)
        if coupon is None:
            db.add(
                Coupon(code=code, percent_off=percent_off, description=description,
                       min_subtotal=min_subtotal)
            )
        elif coupon.min_subtotal is None and min_subtotal is not None:
            coupon.min_subtotal = min_subtotal

    if get_settings().demo_data_enabled:
        if not db.scalar(select(func.count(Testimonial.id))):
            for name, role, quote, avatar, rating, order in TESTIMONIALS:
                db.add(Testimonial(name=name, role=role, quote=quote,
                                   avatar_url=avatar, rating=rating, sort_order=order))

        if not db.scalar(select(func.count(ComingSoonItem.id))):
            for title, description, image_url, eta, order in COMING_SOON:
                db.add(ComingSoonItem(title=title, description=description,
                                      image_url=image_url, eta_label=eta, sort_order=order))

    db.commit()


def _bootstrap_admin(db, settings) -> None:
    """Create the shop's one real login when demo data is off."""
    if not settings.admin_email or not settings.admin_password:
        raise RuntimeError(
            "No admin to create: demo data is off, so ADMIN_EMAIL and "
            "ADMIN_PASSWORD must be set for the first admin account. Add them "
            "to the environment (or backend/.env) and start again."
        )
    db.add(
        User(
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            first_name="Djini",
            last_name="Rahman",
            role="admin",
        )
    )


def _ensure_admin(db, settings) -> None:
    """The shop must never be left without an admin — the desk becomes
    unreachable and nobody can promote anyone back. If role edits ever drain
    the admin list (an admin demoting their own account from the Users
    page), every boot restores the account the environment names (or the
    demo admin in a dev database)."""
    if db.scalar(select(func.count(User.id)).where(User.role == "admin")):
        return
    wanted = settings.admin_email or (
        "admin@djinisbakehouse.com" if settings.demo_data_enabled else ""
    )
    user = db.scalar(select(User).where(User.email == wanted)) if wanted else None
    if user is not None:
        user.role = "admin"
        db.commit()
        logger.warning("No admin accounts were left — restored %s as admin on boot", user.email)
    elif settings.admin_email and settings.admin_password:
        _bootstrap_admin(db, settings)
        db.commit()
        logger.warning("No admin accounts were left — created %s as admin on boot", settings.admin_email)
    elif settings.is_production:
        raise RuntimeError(
            "No admin accounts left, and ADMIN_EMAIL doesn't match an existing "
            "user — nobody could reach the admin desk. Point ADMIN_EMAIL at an "
            "existing account (and set ADMIN_PASSWORD to create it) and start again."
        )
    else:
        logger.warning("No admin accounts left — set ADMIN_EMAIL to have boot restore one")


def seed_db() -> None:
    settings = get_settings()
    if settings.is_production and settings.demo_data_enabled:
        raise RuntimeError(
            "SEED_DEMO_DATA=true would seed known demo logins into a production "
            "app. Unset it (or set it to false) and start again."
        )

    db = SessionLocal()
    try:
        if db.scalar(select(func.count(Product.id))):
            _restore_promised_content(db)
            _ensure_admin(db, settings)
            return  # already seeded

        categories: dict[str, Category] = {}
        for name, slug, image_url, order in CATEGORIES:
            category = Category(name=name, slug=slug, image_url=image_url,
                                sort_order=order)
            db.add(category)
            categories[slug] = category
        db.flush()

        products: dict[str, Product] = {}
        for (name, slug, cat_slug, price, sale_price, featured, stock, prep,
             description, ingredients, allergens) in PRODUCTS:
            product = Product(
                name=name,
                slug=slug,
                description=description,
                price=price,
                sale_price=sale_price,
                category_id=categories[cat_slug].id,
                is_featured=featured,
                is_available=True,
                stock=stock,
                prep_time_minutes=prep,
                image_url=f"/static/products/{slug}.jpg",
                ingredients=ingredients,
                allergens=allergens,
            )
            db.add(product)
            products[slug] = product
        db.flush()

        # The storefront copy promises these codes — they belong to the shop,
        # not to the demo dataset.
        coupons: dict[str, Coupon] = {}
        for code, percent_off, description, min_subtotal in COUPONS:
            coupon = Coupon(code=code, percent_off=percent_off, description=description,
                            min_subtotal=min_subtotal)
            db.add(coupon)
            coupons[code] = coupon

        if settings.demo_data_enabled:
            users: dict[str, User] = {}
            for email, password, first_name, last_name, role in DEMO_USERS:
                user = User(
                    email=email,
                    password_hash=hash_password(password),
                    first_name=first_name,
                    last_name=last_name,
                    role=role,
                )
                db.add(user)
                users[email] = user
            db.flush()

            for email, (label, recipient, phone, line1, line2, city, state, postal) in DEMO_ADDRESSES.items():
                db.add(
                    Address(
                        user_id=users[email].id,
                        label=label,
                        recipient=recipient,
                        phone=phone,
                        line1=line1,
                        line2=line2,
                        city=city,
                        state=state,
                        postal_code=postal,
                        country="USA",
                        is_default=True,
                    )
                )

            for slug, email, rating, days_ago, comment in REVIEWS:
                db.add(
                    Review(
                        product_id=products[slug].id,
                        user_id=users[email].id,
                        rating=rating,
                        comment=comment,
                        created_at=utcnow() - timedelta(days=days_ago, hours=rating),
                    )
                )

            for index, seq in enumerate(ORDER_HISTORY):
                _seed_order(db, users, products, coupons, index, seq)

            for name, role, quote, avatar, rating, order in TESTIMONIALS:
                db.add(Testimonial(name=name, role=role, quote=quote,
                                   avatar_url=avatar, rating=rating, sort_order=order))

            for title, description, image_url, eta, order in COMING_SOON:
                db.add(ComingSoonItem(title=title, description=description,
                                      image_url=image_url, eta_label=eta, sort_order=order))

            logger.info("Seeded catalog + demo dataset (demo users, orders, reviews)")
        else:
            _bootstrap_admin(db, settings)
            logger.info("Seeded catalog only — fresh production start, admin login from ADMIN_EMAIL")

        db.commit()
    finally:
        db.close()
