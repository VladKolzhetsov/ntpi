import unittest

import mylib as m


class T(unittest.TestCase):
    def test_maximum(self):
        self.assertEqual(m.maximum([3, 9, 2]), 9)

    def test_minimum(self):
        self.assertEqual(m.minimum([3, 9, 2]), 2)

    def test_clamp(self):
        self.assertEqual(m.clamp(15, 0, 10), 10)


if __name__ == '__main__':
    unittest.main()
