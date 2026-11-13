import unittest

import mylib as m


class T(unittest.TestCase):
    def test_add_tax(self):
        self.assertEqual(m.add_tax(100), 120.0)


    def test_total_with_tax(self):
        self.assertEqual(m.total_with_tax([100, 50]), 180.0)



if __name__ == '__main__':
    unittest.main()
