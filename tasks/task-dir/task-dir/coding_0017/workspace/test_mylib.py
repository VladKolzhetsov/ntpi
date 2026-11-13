import unittest

import mylib as m


class T(unittest.TestCase):
    def test_average(self):
        self.assertEqual(m.average([2, 4, 6]), 4)

    def test_average_empty(self):
        self.assertEqual(m.average([]), 0)

    def test_median_even(self):
        self.assertEqual(m.median([1, 2, 3, 4]), 2.5)

    def test_spread_single(self):
        self.assertEqual(m.spread([5]), 0)


if __name__ == '__main__':
    unittest.main()
