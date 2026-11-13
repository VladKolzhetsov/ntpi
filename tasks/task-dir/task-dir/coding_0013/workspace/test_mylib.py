import unittest

import mylib as m


class T(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(m.normalize([1, 1, 2]), [0.25, 0.25, 0.5])

    def test_shares(self):
        self.assertEqual(m.shares([1, 1, 2]), [25.0, 25.0, 50.0])

    def test_biggest_share(self):
        self.assertEqual(m.biggest_share([1, 1, 2]), 50.0)


if __name__ == '__main__':
    unittest.main()
