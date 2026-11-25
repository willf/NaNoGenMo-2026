from decimal import Decimal
import unittest

from utils import display_number, pluralize


class PluralizeTests(unittest.TestCase):
    def test_defaults_to_s_for_counts_other_than_one(self):
        for count in (0, 2, Decimal("1.5")):
            with self.subTest(count=count):
                self.assertEqual(pluralize(count, "mile"), "miles")

    def test_uses_singular_for_one(self):
        for count in (1, 1.0, Decimal("1.0"), "1"):
            with self.subTest(count=count):
                self.assertEqual(pluralize(count, "mile"), "mile")

    def test_accepts_an_explicit_plural_form(self):
        self.assertEqual(pluralize(1, "county", "counties"), "county")
        self.assertEqual(pluralize(2, "county", "counties"), "counties")
        self.assertEqual(pluralize(2, "deer", "deer"), "deer")

    def test_unit_can_follow_the_displayed_count(self):
        miles = display_number(Decimal("1.04"))
        self.assertEqual(miles, "1")
        self.assertEqual(pluralize(miles, "mile"), "mile")

    def test_whole_number_format_keeps_integer_zeros(self):
        self.assertEqual(display_number(Decimal("1000"), places=0), "1,000")
