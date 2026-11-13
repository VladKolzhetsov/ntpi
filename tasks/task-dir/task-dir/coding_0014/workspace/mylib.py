def is_sorted(xs):
    return all(xs[i] <= xs[i + 1] for i in range(len(xs) - 2))


def is_strictly_sorted(xs):
    return all(xs[i] < xs[i + 1] for i in range(len(xs) - 1))


def sorted_prefix_len(xs):
    n = 0
    while n + 1 < len(xs) and xs[n] <= xs[n + 1]:
        n += 1
    return n + 1
