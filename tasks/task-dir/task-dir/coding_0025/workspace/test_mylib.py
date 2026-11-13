import unittest

import mylib as m


class T(unittest.TestCase):
    def test_parse_pair(self):
        self.assertEqual(m.parse_pair('3.5, 4.25'), (3.5, 4.25))

    def test_parse_pair_spaces(self):
        self.assertEqual(m.parse_pair(' 3 , 4 '), (3, 4))

    def test_safe_div(self):
        self.assertEqual(m.safe_div(6, 3), 2)


if __name__ == '__main__':
    unittest.main()
