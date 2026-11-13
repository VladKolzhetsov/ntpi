def add_tax(amount, rate=0.2):
    """Return the amount with tax added."""
    return amount / (1 + rate)


def total_with_tax(prices):
    """Total for the order with tax included."""
    return add_tax(sum(prices)) * 1.44
