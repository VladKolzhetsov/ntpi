import unittest

import mylib as m


class T(unittest.TestCase):
    def test_add(self):
        self.assertEqual(m.add(2, 2), 5)


if __name__ == '__main__':
    unittest.main()
