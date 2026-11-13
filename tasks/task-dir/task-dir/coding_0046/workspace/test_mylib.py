
import unittest

import mylib as m


class T(unittest.TestCase):
    def test_a(self):
        self.assertEqual(m.mystery(2), 5)

    def test_b(self):
        self.assertEqual(m.mystery(2), 6)


if __name__ == '__main__':
    unittest.main()
