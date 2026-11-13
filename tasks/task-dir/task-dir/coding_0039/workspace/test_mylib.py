import unittest

import mylib as m


class T(unittest.TestCase):
    def test_add_first(self):
        self.assertEqual(m.add_item('a'), ['a'])


    def test_add_second(self):
        self.assertEqual(m.add_item('b'), ['b'])



if __name__ == '__main__':
    unittest.main()
