from __future__ import annotations

from decimal import Decimal, InvalidOperation


def format_brl_from_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    value = Decimal(abs(cents)) / Decimal("100")
    whole, _, fraction = f"{value:.2f}".partition(".")
    groups: list[str] = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    return f"{sign}R$ {'.'.join(reversed(groups))},{fraction}"


def parse_amount_to_cents(raw_amount: str) -> int:
    normalized = raw_amount.strip().replace("R$", "").replace(" ", "")
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    if not normalized:
        raise ValueError("Amount is empty.")

    try:
        value = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Amount is invalid.") from exc

    if value <= 0:
        raise ValueError("Amount must be greater than zero.")

    cents = int((value * 100).quantize(Decimal("1")))
    if cents <= 0:
        raise ValueError("Amount must be greater than zero.")
    return cents
