from datetime import datetime, time
from decimal import Decimal as D

import pytest

from app.domain import commission
from app.domain import negotiation as neg
from app.domain.orders import (
    By,
    InvalidTransition,
    LineAmount,
    OrderAction,
    allowed_actions,
    compute_totals,
    delivery_date,
    delivery_slots,
    express_problem,
    is_valid_qty,
    transition,
)
from app.models import OrderStatus as St

# --- order lifecycle ---------------------------------------------------------------------------------------


def test_happy_path_transitions():
    s = St.negotiating
    s = transition(s, OrderAction.agree, By.system)
    s = transition(s, OrderAction.confirm, By.seller)
    s = transition(s, OrderAction.ship, By.seller)
    s = transition(s, OrderAction.deliver, By.seller)
    assert transition(s, OrderAction.accept, By.buyer) == St.accepted


@pytest.mark.parametrize(
    "status,action,by",
    [
        (St.placed, OrderAction.confirm, By.buyer),  # wrong side
        (St.confirmed, OrderAction.cancel, By.buyer),  # too late to cancel
        (St.accepted, OrderAction.cancel, By.buyer),
        (St.negotiating, OrderAction.confirm, By.seller),  # seller must answer the offer first
        (St.delivered, OrderAction.accept, By.seller),
    ],
)
def test_forbidden_transitions(status, action, by):
    with pytest.raises(InvalidTransition):
        transition(status, action, by)


def test_allowed_actions_per_side():
    assert set(allowed_actions(St.placed, By.seller)) == {OrderAction.confirm, OrderAction.reject}
    assert allowed_actions(St.placed, By.buyer) == [OrderAction.cancel]
    assert allowed_actions(St.accepted, By.buyer) == []


def test_totals_with_discount_and_delivery():
    lines = [LineAmount(D(12), 3300), LineAmount(D("0.5"), 2000)]
    t = compute_totals(lines, delivery_fee_amd=1000, free_delivery_from_amd=50_000)
    assert (t.subtotal_amd, t.discount_amd, t.delivery_fee_amd, t.total_amd) == (40_600, 0, 1000, 41_600)
    # 5% discount; goods after discount still below the free-delivery threshold
    t = compute_totals(lines, discount_bp=500, delivery_fee_amd=1000, free_delivery_from_amd=40_000)
    assert (t.discount_amd, t.delivery_fee_amd, t.total_amd) == (2030, 1000, 39_570)
    assert (
        compute_totals(
            [LineAmount(D(30), 2000)], delivery_fee_amd=1000, free_delivery_from_amd=50_000
        ).total_amd
        == 60_000
    )


def test_empty_order_has_no_delivery_fee():
    assert compute_totals([], delivery_fee_amd=1000).total_amd == 0


def test_delivery_date_cutoff_and_weekdays():
    tue_morning = datetime(2026, 10, 20, 10, 0)  # Tuesday
    tue_evening = datetime(2026, 10, 20, 17, 0)
    assert delivery_date(tue_morning, time(16), 1).isoformat() == "2026-10-21"
    assert delivery_date(tue_evening, time(16), 1).isoformat() == "2026-10-22"
    sat_evening = datetime(2026, 10, 24, 18, 0)
    assert delivery_date(sat_evening, time(16), 1, weekdays={1, 2, 3, 4, 5, 6}).isoformat() == "2026-10-26"


def test_small_order_fee_and_express():
    lines = [LineAmount(D(7), 550)]  # 3 850, below a 10 000 minimum
    t = compute_totals(lines, delivery_fee_amd=1000, min_order_amd=10_000, small_order_fee_amd=2500)
    assert (t.delivery_fee_amd, t.total_amd) == (2500, 6350)
    # urgent delivery is paid even when delivery itself is free
    t = compute_totals(
        [LineAmount(D(30), 2000)], delivery_fee_amd=1000, free_delivery_from_amd=50_000, express_fee_amd=3000
    )
    assert (t.delivery_fee_amd, t.total_amd) == (3000, 63_000)


def test_delivery_slots():
    assert delivery_slots(time(9), time(12), 1) == [
        (time(9), time(10)),
        (time(10), time(11)),
        (time(11), time(12)),
    ]
    assert delivery_slots(time(9), time(12), 2) == [(time(9), time(11))]
    assert delivery_slots(time(9), time(12), 0) == []


def test_express_conditions():
    tue = datetime(2026, 10, 20, 10, 0)
    assert express_problem(tue, time(12), None, 6000, 5000) is None
    assert express_problem(tue.replace(hour=13), time(12), None, 6000, 5000) == "express_too_late"
    assert express_problem(tue, time(12), {1, 3}, 6000, 5000) == "express_not_today"
    assert express_problem(tue, time(12), None, 4000, 5000) == "express_below_min"


def test_qty_steps():
    assert is_valid_qty(D("1.5"), D("0.5"), D("0.5"))
    assert not is_valid_qty(D("1.2"), D("0.5"), D("0.5"))
    assert not is_valid_qty(D(4), D(5), D(1))


# --- negotiation rules -------------------------------------------------------------------------------------


def test_buyer_price_checks():
    assert neg.check_buyer_price(3300, 3500, D(12), 20_000) is None
    assert neg.check_buyer_price(3500, 3500, D(12), 20_000) == "offer_not_below_price"
    assert neg.check_buyer_price(1700, 3500, D(12), 20_000) == "offer_too_low"
    assert neg.check_buyer_price(3300, 3500, D(5), 20_000) == "line_too_small_for_offer"
    assert neg.check_buyer_price(3200, 3500, D(12), 0, previous_buyer=3200) == "offer_must_increase"
    assert neg.check_buyer_price(3300, 3500, D(12), 0, seller_counter=3300) == "offer_not_below_counter"


def test_seller_price_checks():
    assert neg.check_seller_price(3300, 3500, 3100) is None
    assert neg.check_seller_price(3500, 3500, 3100) == "counter_not_below_price"
    assert neg.check_seller_price(3100, 3500, 3100) == "counter_not_above_offer"
    assert neg.check_seller_price(3300, 3500, 3200, previous_seller=3300) == "counter_must_decrease"


def test_percent_checks():
    assert neg.check_buyer_percent(700, 50_000, 20_000) is None
    assert neg.check_buyer_percent(700, 10_000, 20_000) == "order_too_small_for_offer"
    assert neg.check_buyer_percent(6000, 50_000, 0) == "bad_discount"
    assert neg.check_seller_percent(500, 700) is None
    assert neg.check_seller_percent(700, 700) == "counter_not_above_offer"


# docs/02-mvp.md §6.4: lori cheese, list 3 500, floor 3 000, auto-accept from 3 300
LORI = neg.AutoRule(floor_price_amd=3000, accept_price_amd=3300, min_line_amd=20_000)


@pytest.mark.parametrize(
    "proposed,verdict,price",
    [
        (3300, neg.Verdict.accept, 3300),  # example 1
        (3400, neg.Verdict.accept, 3400),
        (3100, neg.Verdict.counter, 3300),  # example 2: midpoint 3300
        (3000, neg.Verdict.counter, 3300),  # midpoint 3250 < accept -> accept price
        (2000, neg.Verdict.decline, None),  # example 4: below the floor
    ],
)
def test_autopilot_examples(proposed, verdict, price):
    d = neg.decide(proposed, 3500, D(12), LORI, client_orders=0)
    assert (d.verdict, d.price_amd) == (verdict, price)


def test_autopilot_hands_over_to_human():
    assert neg.decide(3100, 3500, D(12), None, 0).verdict == neg.Verdict.manual
    assert (
        neg.decide(3100, 3500, D(12), neg.AutoRule(only_regulars=True, accept_price_amd=3300), 2).verdict
        == "manual"
    )
    assert (
        neg.decide(3100, 3500, D(12), neg.AutoRule(min_qty=D(20), accept_price_amd=3300), 0).verdict
        == "manual"
    )
    no_counter = neg.AutoRule(floor_price_amd=3000, accept_price_amd=3300, auto_counter=False)
    assert neg.decide(3100, 3500, D(12), no_counter, 0).verdict == neg.Verdict.manual


def test_autopilot_percent_rule_and_rounding():
    # Seller-wide rule: floor 90%, accept 95% of the price
    rule = neg.AutoRule(floor_pct_bp=9000, accept_pct_bp=9500)
    assert neg.decide(4300, 4500, D(10), rule, 0).verdict == neg.Verdict.accept  # 4275 needed
    d = neg.decide(4100, 4500, D(10), rule, 0)
    assert (d.verdict, d.price_amd) == (neg.Verdict.counter, 4300)  # midpoint 4300 >= 4275
    assert neg.decide(4000, 4500, D(10), rule, 0).verdict == neg.Verdict.decline  # below 4050


def test_autopilot_small_line_declines():
    d = neg.decide(3300, 3500, D(5), LORI, 0)
    assert (d.verdict, d.reason) == (neg.Verdict.decline, "line_too_small_for_offer")


# --- commission --------------------------------------------------------------------------------------------


def test_commission_weighted():
    c = commission.compute([(40_000, 1000), (10_000, 400)])
    assert (c.amount_amd, c.rate_bp) == (4400, 880)
    c = commission.compute([(40_000, 1000)], first_order_bonus_bp=250)
    assert (c.amount_amd, c.rate_bp) == (5000, 1250)
    assert commission.compute([]).amount_amd == 0
