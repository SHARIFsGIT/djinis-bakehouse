"""The shop's one loyalty definition — shared by the admin roll and the
delivery rules, so a "VIP" on the desk's screen and a "VIP" at checkout are
the same person by the same measure.

Loyalty, in words the baker can defend to a customer's face: kept orders
(cancelled ones never count) and a visit inside the window. Three kept
orders earns "loyal"; ten orders earns "vip"; anyone who earned a tier and
then went quiet for the window is "lapsed" — exactly who a win-back
voucher is for.
"""

from .config import get_settings
from .database import utcnow

LOYAL_ORDERS = 3
VIP_ORDERS = 10
LOYAL_WINDOW_DAYS = 90


def loyalty_tier(order_count: int, spend: float, last_order_at) -> str:
    """The tier those facts earn: vip | loyal | lapsed | regular.

    `spend` is lifetime spend in the shop's *active* currency; the
    spend-based VIP route stays off until an approved threshold exists
    (VIP_SPEND_THRESHOLD), so order counts carry the tiers alone — per the
    EUR migration's no-relabelled-dollars rule."""
    vip_spend = get_settings().vip_spend_threshold
    if last_order_at is None:
        return "regular"
    days_since = (utcnow() - last_order_at).days
    fresh = days_since <= LOYAL_WINDOW_DAYS
    spend_is_vip = vip_spend is not None and spend >= float(vip_spend)
    if spend_is_vip or order_count >= VIP_ORDERS:
        return "vip" if fresh else "lapsed"
    if order_count >= LOYAL_ORDERS:
        return "loyal" if fresh else "lapsed"
    return "regular"
