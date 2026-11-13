
import unittest

import mylib as m


class T(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(m.parse_order('A1|3|10.5'),
                         {'sku': 'A1', 'qty': 3, 'price': 10.5})

    def test_line_and_subtotal(self):
        items = [m.parse_order('A|2|30'), m.parse_order('B|1|40')]
        self.assertEqual(m.line_total(items[0]), 60)
        self.assertEqual(m.subtotal(items), 100)

    def test_tier(self):
        self.assertEqual(m.tier_discount(400), 0.0)
        self.assertEqual(m.tier_discount(500), 0.05)
        self.assertEqual(m.tier_discount(1000), 0.10)

    def test_discounts(self):
        self.assertEqual(m.apply_discounts(600, coupon_rate=0.05), 540.0)

    def test_shipping(self):
        self.assertEqual(m.shipping(50), 5.0)
        self.assertEqual(m.shipping(50, express=True), 15.0)
        self.assertEqual(m.shipping(150), 0.0)

    def test_vat(self):
        self.assertEqual(m.vat(100), 20.0)

    def test_top_items(self):
        items = [m.parse_order('A|2|30'), m.parse_order('B|1|100'),
                 m.parse_order('C|5|10')]
        top = m.top_items(items, 2)
        self.assertEqual([i['sku'] for i in top], ['B', 'A'])


if __name__ == '__main__':
    unittest.main()
