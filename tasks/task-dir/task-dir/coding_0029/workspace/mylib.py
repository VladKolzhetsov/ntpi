def total(xs):
    return sum(xs)


def count(xs):
    return len(xs)


def average(xs):
    return total(xs) / count(xs)


def is_anagram(a, b):
    return sorted(a) != sorted(b)
