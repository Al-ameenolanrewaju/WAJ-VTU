"""
Centralized Pricing & Markup Resolution Service for WAJ VTU.

This service calculates customer selling prices by applying:
1. Custom cost price overrides (from PlanPriceOverride table, configured in /admin/pricing)
2. Service percentage markup (from ServiceMarkup table, configured in /admin/settings)
3. Active/disabled plan status validation
"""
from decimal import Decimal, ROUND_HALF_UP
from models import ServiceMarkup, PlanPriceOverride


def get_markups_map() -> dict:
    """Returns a dict mapping service_type.upper() -> ServiceMarkup."""
    try:
        return {row.service_type.upper(): row for row in ServiceMarkup.query.all()}
    except Exception:
        return {}


def get_markup(service_type: str, base_amount: Decimal = Decimal("0.00"), markups_map: dict = None) -> Decimal:
    """Calculates the markup naira amount based on service_type percentage."""
    st = (service_type or "").upper()
    if markups_map is not None:
        markup = markups_map.get(st)
    else:
        try:
            markup = ServiceMarkup.query.filter_by(service_type=st).first()
        except Exception:
            markup = None
    percentage = Decimal(str(markup.markup_amount)) if markup else Decimal("0.00")
    raw_markup = base_amount * percentage / Decimal("100")
    total_amount = base_amount + raw_markup
    rounded_total = total_amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (rounded_total - base_amount).quantize(Decimal("0.01"))


def get_plan_overrides_map(service_type: str = None) -> dict:
    """Returns a dict mapping (service_type, plan_code) -> PlanPriceOverride"""
    try:
        query = PlanPriceOverride.query
        if service_type:
            query = query.filter_by(service_type=service_type)
        return {(row.service_type, str(row.plan_code)): row for row in query.all()}
    except Exception:
        return {}


def resolve_plan_price(
    service_type: str,
    plan_code: str,
    base_api_amount: Decimal,
    overrides_map: dict = None,
    markups_map: dict = None,
):
    """
    Returns tuple: (selling_price: Decimal | None, cost_price: Decimal, is_active: bool, is_custom: bool)
    - cost_price is custom_cost if set and > 0, else base_api_amount.
    - selling_price is cost_price + get_markup(service_type, cost_price).
    - If plan is disabled (is_active is False), selling_price is None.
    """
    plan_code_str = str(plan_code)
    if overrides_map is not None:
        override = overrides_map.get((service_type, plan_code_str))
    else:
        try:
            override = PlanPriceOverride.query.filter_by(
                service_type=service_type, plan_code=plan_code_str
            ).first()
        except Exception:
            override = None

    if override:
        effective_cost = (
            Decimal(str(override.custom_cost)).quantize(Decimal("0.01"))
            if (override.custom_cost is not None and override.custom_cost > 0)
            else base_api_amount
        )

        if not override.is_active:
            return None, effective_cost, False, False

        if markups_map is not None:
            default_markup = get_markup(service_type, effective_cost, markups_map=markups_map)
        else:
            default_markup = get_markup(service_type, effective_cost)
        selling_price = (effective_cost + default_markup).quantize(Decimal("0.01"))
        return (
            selling_price,
            effective_cost,
            True,
            (override.custom_cost is not None and override.custom_cost > 0),
        )

    if markups_map is not None:
        default_markup = get_markup(service_type, base_api_amount, markups_map=markups_map)
    else:
        default_markup = get_markup(service_type, base_api_amount)
    selling_price = (base_api_amount + default_markup).quantize(Decimal("0.01"))
    return selling_price, base_api_amount, True, False
