import unittest

import mylib as m


class T(unittest.TestCase):
    def test_ticks(self):
        m.reset()
        m.tick()
        m.tick()
        self.assertEqual(m.reads(), 2)



if __name__ == '__main__':
    unittest.main()
