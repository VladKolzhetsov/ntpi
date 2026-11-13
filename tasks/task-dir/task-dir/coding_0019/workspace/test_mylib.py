import unittest

import mylib as m


class T(unittest.TestCase):
    def test_title_words(self):
        self.assertEqual(m.title_words('hello wide world'), 'Hello Wide World')

    def test_initials(self):
        self.assertEqual(m.initials('john ronald tolkien'), 'J. R. T.')

    def test_shout_empty(self):
        self.assertEqual(m.shout(''), '!')


if __name__ == '__main__':
    unittest.main()
