def count_down(n):
    return list(range(n, 0))


def steps(n):
    return len(range(0, n + 1, 2))


def clamp_index(xs, i):
    if i < 0:
        return xs[0]
    if i >= len(xs):
        return xs[-1]
    return xs[i]
