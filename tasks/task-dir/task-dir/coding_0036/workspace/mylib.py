"""Text utilities (large module)."""

def clean(s):
    """Collapse whitespace and strip ends."""
    return ' '.join(s.split())

def upper_first(s):
    """Capitalise the first character only."""
    return s[:1].upper() + s[1:]

def title_case(s):
    """Capitalise every word."""
    return ' '.join(w.capitalize() for w in clean(s).split())

def snake(s):
    """Convert to snake_case."""
    return clean(s).lower().replace(' ', '_')

def kebab(s):
    """Convert to kebab-case."""
    return clean(s).lower().replace(' ', '-')

def count_words(s):
    """Number of words."""
    return len(clean(s).split())

def first_word(s):
    """First word or empty string."""
    words = clean(s).split()
    return words[0] if words else ''

def last_word(s):
    """Last word or empty string."""
    words = clean(s).split()
    return words[-1] if words else ''

def wrap(s, width):
    """Greedy word wrap to lines of at most width characters."""
    words = clean(s).split()
    lines, cur = [], ''
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + ' ' + w).strip()
    if cur:
        lines.append(cur)
    return lines

def truncate(s, n):
    """Truncate to at most n characters, appending '.' if cut."""
    return s if len(s) <= n else s[:n] + '.'

def slug(s):
    """URL slug: lowercase alphanumerics separated by single dashes."""
    out = []
    for ch in clean(s).lower():
        if ch.isalnum():
            out.append(ch)
        elif not out or out[-1] != '-':
            out.append('-')
    return ''.join(out).strip('-')

def initials(s):
    """Initials of each word, lowercased."""
    return ''.join(w[0] for w in clean(s).split() if w)

def rev_1(s):
    """Reverse the string (helper #1)."""
    return s[::-1]


def words_1(s):
    """List of words (helper #1)."""
    return clean(s).split()


def ngrams_1(s, n=2):
    """Word n-grams (helper #1)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_2(s):
    """Reverse the string (helper #2)."""
    return s[::-1]


def words_2(s):
    """List of words (helper #2)."""
    return clean(s).split()


def ngrams_2(s, n=2):
    """Word n-grams (helper #2)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_3(s):
    """Reverse the string (helper #3)."""
    return s[::-1]


def words_3(s):
    """List of words (helper #3)."""
    return clean(s).split()


def ngrams_3(s, n=2):
    """Word n-grams (helper #3)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_4(s):
    """Reverse the string (helper #4)."""
    return s[::-1]


def words_4(s):
    """List of words (helper #4)."""
    return clean(s).split()


def ngrams_4(s, n=2):
    """Word n-grams (helper #4)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_5(s):
    """Reverse the string (helper #5)."""
    return s[::-1]


def words_5(s):
    """List of words (helper #5)."""
    return clean(s).split()


def ngrams_5(s, n=2):
    """Word n-grams (helper #5)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_6(s):
    """Reverse the string (helper #6)."""
    return s[::-1]


def words_6(s):
    """List of words (helper #6)."""
    return clean(s).split()


def ngrams_6(s, n=2):
    """Word n-grams (helper #6)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_7(s):
    """Reverse the string (helper #7)."""
    return s[::-1]


def words_7(s):
    """List of words (helper #7)."""
    return clean(s).split()


def ngrams_7(s, n=2):
    """Word n-grams (helper #7)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_8(s):
    """Reverse the string (helper #8)."""
    return s[::-1]


def words_8(s):
    """List of words (helper #8)."""
    return clean(s).split()


def ngrams_8(s, n=2):
    """Word n-grams (helper #8)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_9(s):
    """Reverse the string (helper #9)."""
    return s[::-1]


def words_9(s):
    """List of words (helper #9)."""
    return clean(s).split()


def ngrams_9(s, n=2):
    """Word n-grams (helper #9)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_10(s):
    """Reverse the string (helper #10)."""
    return s[::-1]


def words_10(s):
    """List of words (helper #10)."""
    return clean(s).split()


def ngrams_10(s, n=2):
    """Word n-grams (helper #10)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_11(s):
    """Reverse the string (helper #11)."""
    return s[::-1]


def words_11(s):
    """List of words (helper #11)."""
    return clean(s).split()


def ngrams_11(s, n=2):
    """Word n-grams (helper #11)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_12(s):
    """Reverse the string (helper #12)."""
    return s[::-1]


def words_12(s):
    """List of words (helper #12)."""
    return clean(s).split()


def ngrams_12(s, n=2):
    """Word n-grams (helper #12)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_13(s):
    """Reverse the string (helper #13)."""
    return s[::-1]


def words_13(s):
    """List of words (helper #13)."""
    return clean(s).split()


def ngrams_13(s, n=2):
    """Word n-grams (helper #13)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_14(s):
    """Reverse the string (helper #14)."""
    return s[::-1]


def words_14(s):
    """List of words (helper #14)."""
    return clean(s).split()


def ngrams_14(s, n=2):
    """Word n-grams (helper #14)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_15(s):
    """Reverse the string (helper #15)."""
    return s[::-1]


def words_15(s):
    """List of words (helper #15)."""
    return clean(s).split()


def ngrams_15(s, n=2):
    """Word n-grams (helper #15)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_16(s):
    """Reverse the string (helper #16)."""
    return s[::-1]


def words_16(s):
    """List of words (helper #16)."""
    return clean(s).split()


def ngrams_16(s, n=2):
    """Word n-grams (helper #16)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_17(s):
    """Reverse the string (helper #17)."""
    return s[::-1]


def words_17(s):
    """List of words (helper #17)."""
    return clean(s).split()


def ngrams_17(s, n=2):
    """Word n-grams (helper #17)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_18(s):
    """Reverse the string (helper #18)."""
    return s[::-1]


def words_18(s):
    """List of words (helper #18)."""
    return clean(s).split()


def ngrams_18(s, n=2):
    """Word n-grams (helper #18)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def rev_19(s):
    """Reverse the string (helper #19)."""
    return s[::-1]


def words_19(s):
    """List of words (helper #19)."""
    return clean(s).split()


def ngrams_19(s, n=2):
    """Word n-grams (helper #19)."""
    ws = clean(s).split()
    return [' '.join(ws[j:j + n]) for j in range(max(0, len(ws) - n + 1))]

def pad_1(s, width=10):
    """Right-pad with spaces (helper #1)."""
    return s + ' ' * max(0, width - len(s))


def strip_1(s):
    """Strip whitespace (helper #1)."""
    return s.strip()

def pad_2(s, width=10):
    """Right-pad with spaces (helper #2)."""
    return s + ' ' * max(0, width - len(s))


def strip_2(s):
    """Strip whitespace (helper #2)."""
    return s.strip()

def pad_3(s, width=10):
    """Right-pad with spaces (helper #3)."""
    return s + ' ' * max(0, width - len(s))


def strip_3(s):
    """Strip whitespace (helper #3)."""
    return s.strip()

def pad_4(s, width=10):
    """Right-pad with spaces (helper #4)."""
    return s + ' ' * max(0, width - len(s))


def strip_4(s):
    """Strip whitespace (helper #4)."""
    return s.strip()

def pad_5(s, width=10):
    """Right-pad with spaces (helper #5)."""
    return s + ' ' * max(0, width - len(s))


def strip_5(s):
    """Strip whitespace (helper #5)."""
    return s.strip()

def pad_6(s, width=10):
    """Right-pad with spaces (helper #6)."""
    return s + ' ' * max(0, width - len(s))


def strip_6(s):
    """Strip whitespace (helper #6)."""
    return s.strip()

def pad_7(s, width=10):
    """Right-pad with spaces (helper #7)."""
    return s + ' ' * max(0, width - len(s))


def strip_7(s):
    """Strip whitespace (helper #7)."""
    return s.strip()

def pad_8(s, width=10):
    """Right-pad with spaces (helper #8)."""
    return s + ' ' * max(0, width - len(s))


def strip_8(s):
    """Strip whitespace (helper #8)."""
    return s.strip()

def pad_9(s, width=10):
    """Right-pad with spaces (helper #9)."""
    return s + ' ' * max(0, width - len(s))


def strip_9(s):
    """Strip whitespace (helper #9)."""
    return s.strip()

def pad_10(s, width=10):
    """Right-pad with spaces (helper #10)."""
    return s + ' ' * max(0, width - len(s))


def strip_10(s):
    """Strip whitespace (helper #10)."""
    return s.strip()

def pad_11(s, width=10):
    """Right-pad with spaces (helper #11)."""
    return s + ' ' * max(0, width - len(s))


def strip_11(s):
    """Strip whitespace (helper #11)."""
    return s.strip()

def pad_12(s, width=10):
    """Right-pad with spaces (helper #12)."""
    return s + ' ' * max(0, width - len(s))


def strip_12(s):
    """Strip whitespace (helper #12)."""
    return s.strip()

def pad_13(s, width=10):
    """Right-pad with spaces (helper #13)."""
    return s + ' ' * max(0, width - len(s))


def strip_13(s):
    """Strip whitespace (helper #13)."""
    return s.strip()

def pad_14(s, width=10):
    """Right-pad with spaces (helper #14)."""
    return s + ' ' * max(0, width - len(s))


def strip_14(s):
    """Strip whitespace (helper #14)."""
    return s.strip()

def pad_15(s, width=10):
    """Right-pad with spaces (helper #15)."""
    return s + ' ' * max(0, width - len(s))


def strip_15(s):
    """Strip whitespace (helper #15)."""
    return s.strip()

def pad_16(s, width=10):
    """Right-pad with spaces (helper #16)."""
    return s + ' ' * max(0, width - len(s))


def strip_16(s):
    """Strip whitespace (helper #16)."""
    return s.strip()

def pad_17(s, width=10):
    """Right-pad with spaces (helper #17)."""
    return s + ' ' * max(0, width - len(s))


def strip_17(s):
    """Strip whitespace (helper #17)."""
    return s.strip()

def pad_18(s, width=10):
    """Right-pad with spaces (helper #18)."""
    return s + ' ' * max(0, width - len(s))


def strip_18(s):
    """Strip whitespace (helper #18)."""
    return s.strip()

def pad_19(s, width=10):
    """Right-pad with spaces (helper #19)."""
    return s + ' ' * max(0, width - len(s))


def strip_19(s):
    """Strip whitespace (helper #19)."""
    return s.strip()
