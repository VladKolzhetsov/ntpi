import unittest

import mylib as m


class T(unittest.TestCase):
    def test_in_band(self):
        self.assertTrue(m.in_band(3, 1, 5))
        self.assertTrue(m.in_band(5, 1, 5))
        self.assertFalse(m.in_band(6, 1, 5))


    def test_band_width(self):
        self.assertEqual(m.band_width(1, 5), 5)



if __name__ == '__main__':
    unittest.main()
