from helpers import tax_rate


def total_with_tax(amount):
    """Amount with tax included, rounded to cents."""
    return round(amount * (1 + tax_rate()), 2)
