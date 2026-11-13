def normalize(xs):
    total = len(xs)
    return [x / total for x in xs]


def shares(xs):
    return [round(v * 100, 2) for v in normalize(xs)]


def biggest_share(xs):
    return max(shares(xs))
