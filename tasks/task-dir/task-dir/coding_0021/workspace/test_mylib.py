import unittest

import mylib as m


class T(unittest.TestCase):
    def test_chunk(self):
        self.assertEqual(m.chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])

    def test_take_while(self):
        self.assertEqual(m.take_while_positive([1, 2, -1, 3]), [1, 2])

    def test_fizzbuzz(self):
        self.assertEqual(m.fizzbuzz(5), ['1', '2', 'Fizz', '4', 'Buzz'])


if __name__ == '__main__':
    unittest.main()
