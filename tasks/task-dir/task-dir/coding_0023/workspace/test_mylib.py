import unittest

import mylib as m


class T(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(m.add(2, 3), 5)
        self.assertEqual(m.sub(5, 3), 2)
        self.assertEqual(m.mul(4, 3), 12)
        self.assertEqual(m.div(10, 4), 2.5)

    def test_power(self):
        self.assertEqual(m.power(2, 10), 1024)

    def test_gcd_lcm(self):
        self.assertEqual(m.gcd(12, 18), 6)
        self.assertEqual(m.lcm(4, 6), 12)

    def test_is_prime(self):
        self.assertFalse(m.is_prime(49))
        self.assertTrue(m.is_prime(97))

    def test_factorial(self):
        self.assertEqual(m.factorial(5), 120)

    def test_digits(self):
        self.assertEqual(m.digits(407), [4, 0, 7])


if __name__ == '__main__':
    unittest.main()
