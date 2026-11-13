"""Order pricing engine (large module)."""

TAX_RATE = 0.2


def parse_order(row):
    """Parse a pipe-separated order row into a dict."""
    parts = row.split('|')
    return {'sku': parts[0], 'qty': int(parts[1]), 'price': float(parts[2])}

def line_total(item):
    """Total price for one order line (qty * unit price)."""
    return item['qty'] * item['price']

def subtotal(items):
    """Sum of all line totals."""
    return sum(line_total(i) for i in items)

def tier_discount(total):
    """Volume discount rate based on the raw subtotal."""
    if total >= 1000:
        return 0.10
    if total >= 500:
        return 0.05
    return 0.0

def apply_discounts(total, coupon_rate=0.0, member=False):
    """Return (discounted_total, applied_rate).

    Discounts stack: the volume tier rate is added to the coupon rate.
    """
    base = tier_discount(total)
    if member:
        rate = max(base, coupon_rate)
    else:
        rate = base + coupon_rate
    return total * (1 - rate), rate

def shipping(total, express=False):
    """Shipping fee; free over 100 units of currency."""
    if total <= 0:
        return 0.0
    fee = 15.0 if express else 5.0
    if total >= 100:
        fee = 0.0
    return fee

def vat(amount):
    """Value added tax, rounded to 2 decimals."""
    return round(amount * TAX_RATE, 2)

def grand_total(items, coupon_rate=0.0, member=False, express=False):
    """End-to-end price: subtotal -> discounts -> shipping -> tax."""
    sub = subtotal(items)
    discounted, rate = apply_discounts(sub, coupon_rate, member)
    ship = shipping(discounted, express)
    tax = vat(discounted + ship)
    return round(discounted + ship + tax, 2)

def util_1_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #1)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_1_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #1)."""
    return [x * factor for x in xs]


def util_1_sum(xs):
    """Safe sum (utility #1)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_2_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #2)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_2_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #2)."""
    return [x * factor for x in xs]


def util_2_sum(xs):
    """Safe sum (utility #2)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_3_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #3)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_3_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #3)."""
    return [x * factor for x in xs]


def util_3_sum(xs):
    """Safe sum (utility #3)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_4_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #4)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_4_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #4)."""
    return [x * factor for x in xs]


def util_4_sum(xs):
    """Safe sum (utility #4)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_5_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #5)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_5_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #5)."""
    return [x * factor for x in xs]


def util_5_sum(xs):
    """Safe sum (utility #5)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_6_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #6)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_6_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #6)."""
    return [x * factor for x in xs]


def util_6_sum(xs):
    """Safe sum (utility #6)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_7_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #7)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_7_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #7)."""
    return [x * factor for x in xs]


def util_7_sum(xs):
    """Safe sum (utility #7)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_8_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #8)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_8_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #8)."""
    return [x * factor for x in xs]


def util_8_sum(xs):
    """Safe sum (utility #8)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_9_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #9)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_9_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #9)."""
    return [x * factor for x in xs]


def util_9_sum(xs):
    """Safe sum (utility #9)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_10_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #10)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_10_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #10)."""
    return [x * factor for x in xs]


def util_10_sum(xs):
    """Safe sum (utility #10)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_11_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #11)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_11_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #11)."""
    return [x * factor for x in xs]


def util_11_sum(xs):
    """Safe sum (utility #11)."""
    total = 0
    for x in xs:
        total += x
    return total

def util_12_clamp(x, lo=0.0, hi=100.0):
    """Clamp x into [lo, hi] (utility #12)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def util_12_scale(xs, factor=1.0):
    """Scale every value in xs by factor (utility #12)."""
    return [x * factor for x in xs]


def util_12_sum(xs):
    """Safe sum (utility #12)."""
    total = 0
    for x in xs:
        total += x
    return total

def fmt_1_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #1)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_1_pct(x, places=1):
    """Format a ratio as a percentage (formatter #1)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_2_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #2)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_2_pct(x, places=1):
    """Format a ratio as a percentage (formatter #2)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_3_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #3)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_3_pct(x, places=1):
    """Format a ratio as a percentage (formatter #3)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_4_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #4)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_4_pct(x, places=1):
    """Format a ratio as a percentage (formatter #4)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_5_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #5)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_5_pct(x, places=1):
    """Format a ratio as a percentage (formatter #5)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_6_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #6)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_6_pct(x, places=1):
    """Format a ratio as a percentage (formatter #6)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_7_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #7)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_7_pct(x, places=1):
    """Format a ratio as a percentage (formatter #7)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_8_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #8)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_8_pct(x, places=1):
    """Format a ratio as a percentage (formatter #8)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_9_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #9)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_9_pct(x, places=1):
    """Format a ratio as a percentage (formatter #9)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_10_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #10)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_10_pct(x, places=1):
    """Format a ratio as a percentage (formatter #10)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_11_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #11)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_11_pct(x, places=1):
    """Format a ratio as a percentage (formatter #11)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)

def fmt_12_money(x, symbol='$'):
    """Format x as money with 2 decimals (formatter #12)."""
    return symbol + ('%.2f' % round(x, 2))


def fmt_12_pct(x, places=1):
    """Format a ratio as a percentage (formatter #12)."""
    return ('%.' + str(places) + 'f') + '%%' % (x * 100)
