"""
Budget Normalization Service for UtsavAI.
Guarantees mathematical integrity:
1. Percentages sum to exactly 100.0%.
2. Amounts sum to exactly total_budget_inr down to the rupee.
3. Formats Indian numbering system (Lakhs and Crores).
"""

from typing import List
from schemas import BudgetItem


def normalize_budget(items: List[BudgetItem], total_budget_inr: int) -> List[BudgetItem]:
    """
    Normalizes the budget items:
    - Normalizes percentages to sum to exactly 100.0%
    - Recomputes amount_inr from percentage and total_budget_inr
    - Balances rounding discrepancies so the sum of amount_inr exactly matches total_budget_inr
    """
    if not items or total_budget_inr <= 0:
        return items

    # 1. Normalize percentages
    if not items:
        return items

    raw_sum_pct = sum(max(0.0, item.percentage) for item in items)
    if raw_sum_pct <= 0:
        equal_pct = round(100.0 / len(items), 2)
        for item in items:
            item.percentage = equal_pct
    else:
        for item in items:
            item.percentage = round((max(0.0, item.percentage) / raw_sum_pct) * 100.0, 2)

    # Balance percentage rounding to exactly 100.0
    pct_discrepancy = round(100.0 - sum(item.percentage for item in items), 2)
    if abs(pct_discrepancy) > 0.0001:
        # Find item with the largest percentage to absorb slight fraction
        largest_pct_item = max(items, key=lambda x: x.percentage)
        largest_pct_item.percentage = round(largest_pct_item.percentage + pct_discrepancy, 2)

    # 2. Recompute amount_inr based on normalized percentage
    for item in items:
        item.amount_inr = int(round((item.percentage / 100.0) * total_budget_inr))

    # 3. Balance amount rounding discrepancy so sum(amounts) == total_budget_inr exactly
    amount_discrepancy = total_budget_inr - sum(item.amount_inr for item in items)
    if amount_discrepancy != 0:
        largest_amt_item = max(items, key=lambda x: x.amount_inr)
        largest_amt_item.amount_inr += amount_discrepancy

    return items


def adjust_category_budget(
    items: List[BudgetItem],
    category: str,
    delta_percent: float,
    total_budget_inr: int,
) -> List[BudgetItem]:
    """Apply a relative percent change to one category and rebalance other categories."""
    normalized_category = " ".join(category.casefold().split())
    matches = [
        item for item in items
        if normalized_category == " ".join(item.category.casefold().split())
        or normalized_category in item.category.casefold()
    ]
    if len(matches) > 1:
        raise ValueError(f"Budget category is ambiguous: {category}")
    if not matches:
        raise ValueError(f"Budget category not found: {category}")
    target = matches[0]
    if len(items) < 2:
        raise ValueError("At least two budget categories are needed to rebalance an allocation.")

    previous_percentage = target.percentage
    requested_percentage = previous_percentage * (1 + delta_percent / 100)
    target.percentage = min(100.0, max(0.0, requested_percentage))
    remaining = 100.0 - target.percentage
    other_items = [item for item in items if item is not target]
    other_total = sum(max(0.0, item.percentage) for item in other_items)

    for item in other_items:
        if other_total:
            item.percentage = remaining * max(0.0, item.percentage) / other_total
        else:
            item.percentage = remaining / len(other_items)

    return normalize_budget(items, total_budget_inr)


def format_inr(amount: int) -> str:
    """
    Formats an integer amount into standard Indian currency representation.
    E.g. 1500000 -> ₹15,00,000; 30000 -> ₹30,000; 750 -> ₹750
    """
    if amount < 0:
        return f"-{format_inr(abs(amount))}"

    s = str(amount)
    if len(s) <= 3:
        return f"₹{s}"

    last_three = s[-3:]
    remaining = s[:-3]

    parts = []
    while len(remaining) > 2:
        parts.insert(0, remaining[-2:])
        remaining = remaining[:-2]

    if remaining:
        parts.insert(0, remaining)

    return f"₹{','.join(parts)},{last_three}"
