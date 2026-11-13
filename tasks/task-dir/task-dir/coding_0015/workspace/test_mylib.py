import unittest

import mylib as m


class T(unittest.TestCase):
    def test_word_stats(self):
        self.assertEqual(m.word_stats('aa bbb cccc'), ('cccc', 'aa'))

    def test_word_stats_mixed(self):
        self.assertEqual(m.word_stats('x yyyy zz'), ('yyyy', 'x'))


if __name__ == '__main__':
    unittest.main()
