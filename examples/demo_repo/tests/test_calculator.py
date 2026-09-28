import unittest
from calculator import add


class CalculatorTest(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(add(-2, 3), 1)
