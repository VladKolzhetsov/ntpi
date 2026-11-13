import unittest

import mylib as m


class T(unittest.TestCase):
    def test_normalize_name(self):
        self.assertEqual(m.normalize_name(' John  Smith '), 'john_smith')

    def test_initials(self):
        self.assertEqual(m.initials('ada lovelace'), 'AL')

    def test_slugify(self):
        self.assertEqual(m.slugify('Hello World'), 'hello-world')


if __name__ == '__main__':
    unittest.main()
