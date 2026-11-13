import unittest

import mylib as m


class T(unittest.TestCase):
    def test_is_even(self):
        self.assertTrue(m.is_even(4))
        self.assertFalse(m.is_even(7))


    def test_sum_digits(self):
        self.assertEqual(m.sum_digits(123), 6)
        self.assertEqual(m.sum_digits(-9), 9)



if __name__ == '__main__':
    unittest.main()
