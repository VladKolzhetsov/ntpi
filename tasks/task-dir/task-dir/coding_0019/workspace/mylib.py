def title_words(text):
    return text.title()


def initials(text):
    parts = text.split()
    return ' '.join(p[0] + '.' for p in parts)


def shout(text):
    return text.upper() + '!'
