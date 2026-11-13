import unittest

import mylib as m


class T(unittest.TestCase):
    def test_total(self):
        self.assertEqual(m.total([1, 2, 3]), 6)

    def test_average(self):
        self.assertEqual(m.average([2, 4, 8]), 14 / 3)

    def test_median(self):
        self.assertEqual(m.median([3, 1, 2]), 2)

    def test_mode(self):
        self.assertEqual(m.mode([1, 2, 2, 3]), 2)

    def test_title_case(self):
        self.assertEqual(m.title_case('hello world'), 'Hello World')

    def test_caesar(self):
        self.assertEqual(m.caesar('abc', 1), 'bcd')


if __name__ == '__main__':
    unittest.main()
