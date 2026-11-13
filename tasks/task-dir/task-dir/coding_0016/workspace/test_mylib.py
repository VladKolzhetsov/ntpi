import unittest

import mylib as m


class T(unittest.TestCase):
    def test_running_max(self):
        self.assertEqual(m.running_max([5, 2, 8, 1]), [5, 5, 8, 8])

    def test_running_max_negative(self):
        self.assertEqual(m.running_max([-3, -1, -7]), [-3, -1, -1])


if __name__ == '__main__':
    unittest.main()
