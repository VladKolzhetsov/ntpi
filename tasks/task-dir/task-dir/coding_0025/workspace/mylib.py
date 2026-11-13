def parse_pair(s):
    a, b = s.split(',')
    return (a, b)


def maybe_int(s):
    try:
        return int(s)
    except ValueError:
        return None


def safe_div(a, b):
    if b == 0:
        return 0
    return a / b
