import unittest

import mylib as m


class T(unittest.TestCase):
    def test_sorted_true(self):
        self.assertTrue(m.is_sorted([1, 2, 3, 4]))

    def test_sorted_bad_tail(self):
        self.assertFalse(m.is_sorted([1, 2, 4, 3]))

    def test_sorted_two(self):
        self.assertTrue(m.is_sorted([1, 2]))

    def test_prefix(self):
        self.assertEqual(m.sorted_prefix_len([1, 3, 2, 4]), 2)


if __name__ == '__main__':
    unittest.main()
