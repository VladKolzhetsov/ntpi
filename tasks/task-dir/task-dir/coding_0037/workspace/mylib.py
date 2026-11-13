"""Inventory and pricing engine (large module)."""


def parse_order(row):
    """Parse 'SKU|QTY|PRICE' into a dict."""
    parts = row.split('|')
    return {'sku': parts[0], 'qty': int(parts[1]), 'price': float(parts[2])}


def line_total(item):
    """Line total: qty * unit price."""
    return item['qty'] * item['price']


def subtotal(items):
    """Sum of line totals."""
    return util_3_sum([line_total(i) for i in items])


def tier_discount(amount):
    """Discount rate: 0 below 500, 0.05 from 500, 0.10 from 1000."""
    if amount >= 1000:
        return 0.10
    if amount >= 500:
        return 0.05
    return 0.0


def apply_discounts(amount, coupon_rate=0.0):
    """Apply tier discount and coupon (they stack)."""
    rate = tier_discount(amount) + coupon_rate
    return amount * (1 - rate)


def shipping(total, express=False):
    """Shipping cost: 15.0 express, else 5.0, free from 100."""
    if express:
        return 15.0
    return 5.0 if total < 100 else 0.0


def vat(amount):
    """VAT at 20 percent."""
    return round(amount * 0.2, 2)


def top_items(items, n):
    """Top n items by line total, highest first."""
    return sorted(items, key=line_total)[:n]


def util_1_sum(xs):
    """Utility #1: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_1_maxlen(words):
    """Utility #1: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_2_sum(xs):
    """Utility #2: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_2_maxlen(words):
    """Utility #2: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_3_sum(xs):
    """Utility #3: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_3_maxlen(words):
    """Utility #3: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_4_sum(xs):
    """Utility #4: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_4_maxlen(words):
    """Utility #4: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_5_sum(xs):
    """Utility #5: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_5_maxlen(words):
    """Utility #5: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_6_sum(xs):
    """Utility #6: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_6_maxlen(words):
    """Utility #6: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_7_sum(xs):
    """Utility #7: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_7_maxlen(words):
    """Utility #7: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_8_sum(xs):
    """Utility #8: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_8_maxlen(words):
    """Utility #8: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_9_sum(xs):
    """Utility #9: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_9_maxlen(words):
    """Utility #9: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_10_sum(xs):
    """Utility #10: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_10_maxlen(words):
    """Utility #10: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_11_sum(xs):
    """Utility #11: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_11_maxlen(words):
    """Utility #11: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_12_sum(xs):
    """Utility #12: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_12_maxlen(words):
    """Utility #12: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_13_sum(xs):
    """Utility #13: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_13_maxlen(words):
    """Utility #13: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_14_sum(xs):
    """Utility #14: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_14_maxlen(words):
    """Utility #14: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_15_sum(xs):
    """Utility #15: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_15_maxlen(words):
    """Utility #15: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_16_sum(xs):
    """Utility #16: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_16_maxlen(words):
    """Utility #16: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_17_sum(xs):
    """Utility #17: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_17_maxlen(words):
    """Utility #17: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_18_sum(xs):
    """Utility #18: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_18_maxlen(words):
    """Utility #18: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_19_sum(xs):
    """Utility #19: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_19_maxlen(words):
    """Utility #19: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_20_sum(xs):
    """Utility #20: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_20_maxlen(words):
    """Utility #20: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_21_sum(xs):
    """Utility #21: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_21_maxlen(words):
    """Utility #21: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_22_sum(xs):
    """Utility #22: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_22_maxlen(words):
    """Utility #22: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_23_sum(xs):
    """Utility #23: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_23_maxlen(words):
    """Utility #23: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_24_sum(xs):
    """Utility #24: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_24_maxlen(words):
    """Utility #24: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best


def util_25_sum(xs):
    """Utility #25: sum of positive entries."""
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_25_maxlen(words):
    """Utility #25: length of the longest word."""
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best
