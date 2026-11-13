def chunk(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def take_while_positive(xs):
    out = []
    for x in xs:
        if x <= 0:
            out.append(x)
        else:
            break
    return out


def fizzbuzz(n):
    out = []
    for i in range(1, n):
        if i % 15 == 0:
            out.append('FizzBuzz')
        elif i % 3 == 0:
            out.append('Fizz')
        elif i % 5 == 0:
            out.append('Buzz')
        else:
            out.append(str(i))
    return out
