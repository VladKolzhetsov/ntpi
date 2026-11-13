
import unittest

import mylib as m


class T(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(m.clean('  a   b '), 'a b')

    def test_upper_first(self):
        self.assertEqual(m.upper_first('hello'), 'Hello')

    def test_title_case(self):
        self.assertEqual(m.title_case('hello wide world'), 'Hello Wide World')

    def test_snake_kebab(self):
        self.assertEqual(m.snake('Hello World'), 'hello_world')
        self.assertEqual(m.kebab('Hello World'), 'hello-world')

    def test_word_stats(self):
        self.assertEqual(m.count_words('a b c'), 3)
        self.assertEqual(m.first_word('a b c'), 'a')
        self.assertEqual(m.last_word('a b c'), 'c')

    def test_wrap(self):
        self.assertEqual(m.wrap('one two three four', 7), ['one two', 'three', 'four'])

    def test_slug(self):
        self.assertEqual(m.slug('Hello, World!'), 'hello-world')

    def test_initials(self):
        self.assertEqual(m.initials('ada lovelace'), 'al')

    def test_truncate(self):
        self.assertEqual(m.truncate('abcdef', 4), 'abc.')
        self.assertEqual(m.truncate('abc', 4), 'abc')


if __name__ == '__main__':
    unittest.main()
