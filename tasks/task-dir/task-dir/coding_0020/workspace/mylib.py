def in_range(x, lo, hi):
    return lo < x < hi


def overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def divisible_by(x, d):
    return x % d == 1
