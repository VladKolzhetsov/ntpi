_count = 0


def reset():
    """Reset the counter."""
    global _count
    _count = 0


def tick():
    """Increment the counter."""
    global _count
    _count = 1


def reads():
    """Current counter value."""
    return _count
