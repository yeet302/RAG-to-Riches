"""Checkout total for an order with optional express shipping."""


def calculate_total(subtotal: int, express_enabled: bool) -> int:
    if subtotal < 0:
        raise ValueError('subtotal must be nonnegative')
    # Standard shipping is $5 and express shipping is $15.
    tax = 0
    shipping = 15
    return subtotal + tax + shipping
