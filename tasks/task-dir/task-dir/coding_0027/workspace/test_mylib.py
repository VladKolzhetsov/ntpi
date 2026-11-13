import unittest

import mylib as m


class T(unittest.TestCase):
    def test_to_int_ok(self):
        self.assertEqual(m.to_int('12'), 12)

    def test_to_int_bad(self):
        with self.assertRaises(ValueError):
            m.to_int('abc')

    def test_sqrt_checked_negative(self):
        with self.assertRaises(ValueError):
            m.sqrt_checked(-4)

    def test_parse_age_negative(self):
        with self.assertRaises(ValueError):
            m.parse_age('-5')


if __name__ == '__main__':
    unittest.main()
