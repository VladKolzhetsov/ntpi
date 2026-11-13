def divide(a, b):
    return a / b


def index_of(xs, target):
    try:
        return xs.index(target)
    except ValueError:
        return -1


def head(xs):
    return xs[0]
