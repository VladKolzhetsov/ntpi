import unittest

import mylib as m


class T(unittest.TestCase):
    def test_total(self):
        self.assertEqual(m.total([1, 2, 3]), 6)


    def test_maximum(self):
        self.assertEqual(m.maximum([3, 9, 2]), 9)


    def test_count_positive(self):
        self.assertEqual(m.count_positive([1, -2, 0, 3]), 2)


    def test_average(self):
        self.assertEqual(m.average([2, 4, 6]), 4)


    def test_first_char(self):
        self.assertEqual(m.first_char('hello'), 'h')


    def test_reverse_str(self):
        self.assertEqual(m.reverse_str('abc'), 'cba')



if __name__ == '__main__':
    unittest.main()
