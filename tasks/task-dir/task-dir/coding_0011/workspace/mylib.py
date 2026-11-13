def maximum(xs):
    best = xs[0]
    for x in xs:
        if x > best:
            best = x
    return best


def minimum(xs):
    worst = xs[0]
    for x in xs:
        if x > worst:
            worst = x
    return worst


def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
