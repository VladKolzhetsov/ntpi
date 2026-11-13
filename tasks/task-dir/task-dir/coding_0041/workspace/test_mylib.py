import unittest

import mylib as m


class T(unittest.TestCase):
    def test_total_with_tax(self):
        self.assertEqual(m.total_with_tax(100), 120.0)



if __name__ == '__main__':
    unittest.main()
