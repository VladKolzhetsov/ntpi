import unittest

import mylib as m


class T(unittest.TestCase):
    def test_reverse_words(self):
        self.assertEqual(m.reverse_words('a b c'), 'c b a')

    def test_total(self):
        self.assertEqual(m.total([1, 2, 3]), 6)

    def test_count_positive(self):
        self.assertEqual(m.count_positive([-1, 2, 3, -4]), 2)


if __name__ == '__main__':
    unittest.main()
