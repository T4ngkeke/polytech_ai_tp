"""
test_billing.py — [v7.2] weighted-token quota accounting.

Quota is charged on billed tokens = prompt*alpha + completion*beta, so prefill
(prompt) tokens — which are cheaper than decode (completion) — no longer count
the same against a student's daily quota.
"""

from backend.app.services.billing import compute_billed_tokens


def test_default_weights_discount_prefill():
    # alpha=0.2, beta=1.0 → prefill counts at 20%.
    assert compute_billed_tokens(100, 50, alpha=0.2, beta=1.0) == 70


def test_equal_weights_match_raw_sum():
    assert compute_billed_tokens(100, 50, alpha=1.0, beta=1.0) == 150


def test_result_is_rounded_to_int():
    # 7*0.2 = 1.4 → 1 ; 0*1.0 = 0
    result = compute_billed_tokens(7, 0, alpha=0.2, beta=1.0)
    assert result == 1
    assert isinstance(result, int)


def test_zero_prefill_weight_charges_decode_only():
    assert compute_billed_tokens(1000, 30, alpha=0.0, beta=1.0) == 30


def test_never_negative():
    assert compute_billed_tokens(0, 0, alpha=0.2, beta=1.0) == 0
