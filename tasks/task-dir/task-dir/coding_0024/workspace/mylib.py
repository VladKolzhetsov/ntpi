def mean(values):
    return sum(values) / len(values)


def variance(values):
    mu = mean(values)
    return sum((v - mu) ** 2 for v in values) / len(values)


def std(values):
    return variance(values) ** 2


def zscores(values):
    mu = mean(values)
    sd = std(values)
    return [(v - mu) / sd for v in values]
