"""
股票代码标准化工具测试
"""

import unittest
from utils.code_utils import normalize_stock_code, is_index_code, get_market


class TestNormalizeStockCode(unittest.TestCase):
    """股票代码标准化测试"""

    def test_normalize_int_code(self):
        """整数值代码"""
        self.assertEqual(normalize_stock_code(1), '000001')
        self.assertEqual(normalize_stock_code(600000), '600000')
        self.assertEqual(normalize_stock_code(300001), '300001')
        self.assertEqual(normalize_stock_code(688001), '688001')
        self.assertEqual(normalize_stock_code(920680), '920680')

    def test_normalize_six_digit_string(self):
        """6位字符串代码"""
        self.assertEqual(normalize_stock_code('000001'), '000001')
        self.assertEqual(normalize_stock_code('600000'), '600000')
        self.assertEqual(normalize_stock_code('000002'), '000002')

    def test_normalize_with_suffix(self):
        """带后缀的代码"""
        self.assertEqual(normalize_stock_code('000001.SZ'), '000001')
        self.assertEqual(normalize_stock_code('600000.SH'), '600000')
        self.assertEqual(normalize_stock_code('000001.SZ'), '000001')
        self.assertEqual(normalize_stock_code('688001.sh'), '688001')

    def test_normalize_short_string(self):
        """短字符串代码"""
        self.assertEqual(normalize_stock_code('1'), '000001')
        self.assertEqual(normalize_stock_code('600'), '000600')

    def test_normalize_with_whitespace(self):
        """带空格的代码"""
        self.assertEqual(normalize_stock_code(' 000001 '), '000001')
        self.assertEqual(normalize_stock_code('\t600000\n'), '600000')

    def test_normalize_idempotent(self):
        """幂等性测试"""
        code = normalize_stock_code(1)
        self.assertEqual(normalize_stock_code(code), '000001')
        code = normalize_stock_code('000001.SZ')
        self.assertEqual(normalize_stock_code(code), '000001')

    def test_normalize_float(self):
        """浮点数值代码"""
        self.assertEqual(normalize_stock_code(1.0), '000001')
        self.assertEqual(normalize_stock_code(600000.0), '600000')

    def test_normalize_nan(self):
        """NaN 值"""
        import math
        self.assertEqual(normalize_stock_code(float('nan')), '')


class TestIsIndexCode(unittest.TestCase):
    """指数代码判断测试"""

    def test_index_with_suffix(self):
        self.assertTrue(is_index_code('000001.SH'))
        self.assertTrue(is_index_code('000300.SH'))
        self.assertTrue(is_index_code('399001.SZ'))

    def test_stock_without_suffix(self):
        self.assertFalse(is_index_code('000001'))
        self.assertFalse(is_index_code('600000'))

    def test_case_insensitive(self):
        self.assertTrue(is_index_code('000001.sh'))
        self.assertTrue(is_index_code('399001.sz'))


class TestGetMarket(unittest.TestCase):
    """市场判断测试"""

    def test_shanghai(self):
        self.assertEqual(get_market('600000'), 'SH')
        self.assertEqual(get_market('601000'), 'SH')
        self.assertEqual(get_market('688001'), 'SH')

    def test_shenzhen(self):
        self.assertEqual(get_market('000001'), 'SZ')
        self.assertEqual(get_market('002001'), 'SZ')
        self.assertEqual(get_market('300001'), 'SZ')

    def test_beijing(self):
        self.assertEqual(get_market('800001'), 'BJ')
        self.assertEqual(get_market('920680'), 'BJ')

    def test_empty(self):
        self.assertEqual(get_market(''), '')
        self.assertEqual(get_market('abc'), '')


if __name__ == '__main__':
    unittest.main()