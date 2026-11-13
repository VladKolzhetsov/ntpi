import unittest

import mylib as m


class T(unittest.TestCase):
    def test_divide_ok(self):
        self.assertEqual(m.divide(10, 2), 5)

    def test_divide_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            m.divide(1, 0)

    def test_index_not_found(self):
        with self.assertRaises(ValueError):
            m.index_of([1, 2], 9)

    def test_head_empty(self):
        self.assertIsNone(m.head([]))


if __name__ == '__main__':
    unittest.main()
