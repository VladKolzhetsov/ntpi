import unittest

import mylib as m


class T(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(m.set_age(42), 42)


    def test_zero(self):
        with self.assertRaises(ValueError):
            m.set_age(0)


    def test_negative(self):
        with self.assertRaises(ValueError):
            m.set_age(-5)



if __name__ == '__main__':
    unittest.main()
