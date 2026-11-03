"""Run against the planted fixtures: each is expected to FAIL before correction."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent / 'fixtures'

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Regressions(unittest.TestCase):
    def test_bug_01(self):
        service = load('service', ROOT/'bug_01'/'service.py')
        with patch.object(service.random, 'random', return_value=0.25):
            self.assertFalse(service.should_fail(True), '25% draw must not fail with 10% configured')

    def test_bug_02(self):
        checkout = load('checkout', ROOT/'bug_02'/'checkout.py')
        self.assertEqual(checkout.calculate_total(100, False), 105)
        self.assertEqual(checkout.calculate_total(100, True), 115)

    def test_bug_03(self):
        pagination = load('pagination', ROOT/'bug_03'/'pagination.py')
        self.assertEqual(pagination.get_page(list(range(12)), 1, 5), [5,6,7,8,9])

if __name__ == '__main__':
    unittest.main()
