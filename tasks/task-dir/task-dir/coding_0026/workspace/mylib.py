def normalize_name(s):
    return s.lower().replace(' ', '_')


def initials(s):
    return ''.join(w[0] for w in s.split())


def slugify(s):
    out = s.lower().replace(' ', '-')
    return out.strip('-')
