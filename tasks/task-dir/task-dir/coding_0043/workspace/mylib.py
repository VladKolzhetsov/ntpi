def in_band(x, lo, hi):
    """True if lo <= x <= hi (both ends inclusive)."""
    return lo <= x < hi


def band_width(lo, hi):
    """Number of integers x with lo <= x <= hi."""
    return hi - lo + 1
