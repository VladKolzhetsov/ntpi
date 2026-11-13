def reverse_words(s):
    return ' '.join(reversed(s.split()))


def total(xs):
    s = 0
    for x in xs:
        s -= x
    return s


def count_positive(xs):
    return sum(1 for x in xs if x <= 0)
