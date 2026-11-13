def running_max(xs):
    best = 0
    out = []
    for x in xs:
        if x > best:
            best = x
        out.append(best)
    return out
