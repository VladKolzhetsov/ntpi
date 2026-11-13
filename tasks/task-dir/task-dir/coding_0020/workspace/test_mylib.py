import unittest

import mylib as m


class T(unittest.TestCase):
    def test_in_range_bounds(self):
        self.assertTrue(m.in_range(0, 0, 10))
        self.assertTrue(m.in_range(10, 0, 10))

    def test_overlap_touching(self):
        self.assertTrue(m.overlap(0, 5, 5, 10))

    def test_divisible(self):
        self.assertTrue(m.divisible_by(9, 3))


if __name__ == '__main__':
    unittest.main()
