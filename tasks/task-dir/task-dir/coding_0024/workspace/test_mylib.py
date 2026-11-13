import unittest

import mylib as m


class T(unittest.TestCase):
    def test_mean_variance(self):
        self.assertEqual(m.mean([2, 4, 6]), 4)
        self.assertAlmostEqual(m.variance([2, 4, 6]), 8 / 3)

    def test_std(self):
        self.assertAlmostEqual(m.std([2, 4, 6]), (8 / 3) ** 0.5)

    def test_zscores_sum_zero(self):
        self.assertAlmostEqual(sum(m.zscores([2, 4, 6])), 0)


if __name__ == '__main__':
    unittest.main()
