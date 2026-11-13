import unittest

import mylib as m


class T(unittest.TestCase):
    def test_count_down(self):
        self.assertEqual(m.count_down(3), [3, 2, 1])

    def test_steps(self):
        self.assertEqual(m.steps(6), 3)

    def test_clamp_index_last(self):
        self.assertEqual(m.clamp_index([10, 20, 30], 99), 30)


if __name__ == '__main__':
    unittest.main()
