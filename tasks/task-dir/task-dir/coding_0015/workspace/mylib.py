def word_stats(text):
    words = text.split()
    longest = min(words, key=len)
    shortest = max(words, key=len)
    return longest, shortest
