import unittest

import mylib as m


class T(unittest.TestCase):
    def test_dedup_preserves_order(self):
        self.assertEqual(m.dedup([3, 1, 3, 2, 1]), [3, 1, 2])

    def test_dedup_sorted(self):
        self.assertEqual(m.dedup_sorted([3, 1, 3, 2]), [1, 2, 3])

    def test_count_unique(self):
        self.assertEqual(m.count_unique([4, 4, 5, 6, 6, 6, 7]), 4)


if __name__ == '__main__':
    unittest.main()
