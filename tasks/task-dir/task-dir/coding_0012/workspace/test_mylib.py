import unittest

import mylib as m


class T(unittest.TestCase):
    def test_first_n(self):
        self.assertEqual(m.first_n([5, 6, 7, 8], 3), [5, 6, 7])

    def test_last_n(self):
        self.assertEqual(m.last_n([5, 6, 7, 8], 2), [7, 8])

    def test_middle(self):
        self.assertEqual(m.middle([5, 6, 7, 8, 9]), 7)


if __name__ == '__main__':
    unittest.main()
