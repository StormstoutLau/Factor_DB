"""
因子计算器测试
"""

import unittest

import numpy as np
import pandas as pd

from adapters.factor_calculator import FactorCalculator


class TestFactorCalculator(unittest.TestCase):
    """FactorCalculator 测试"""

    @staticmethod
    def _make_price_df():
        """构造测试数据: 2只股票, 30天"""
        np.random.seed(42)
        dates = pd.date_range('2024-01-01', periods=30, freq='B')
        stocks = ['000001.SZ', '000002.SZ']
        data = []
        for stock in stocks:
            base = 50 if stock == '000001.SZ' else 100
            price = base + np.cumsum(np.random.randn(30) * 2)
            for i, d in enumerate(dates):
                data.append({
                    'trade_date': d,
                    'stock_code': stock,
                    'close': price[i],
                    'volume': np.random.randint(1000, 10000),
                    'high': price[i] * np.random.uniform(1.01, 1.05),
                    'low': price[i] * np.random.uniform(0.95, 0.99),
                })
        return pd.DataFrame(data)

    def setUp(self):
        self.df = self._make_price_df()

    # ===== 动量因子 =====

    def test_momentum(self):
        result = FactorCalculator.momentum(self.df, periods=[5, 10])
        self.assertIn('momentum_5', result.columns)
        self.assertIn('momentum_10', result.columns)
        self.assertEqual(len(result), len(self.df))

    def test_momentum_values(self):
        df = self.df[self.df['stock_code'] == '000001.SZ'].copy()
        result = FactorCalculator.momentum(df, periods=[5])
        # 前5天为 NaN
        self.assertTrue(result['momentum_5'].iloc[:4].isna().all())
        # 第6天有值
        self.assertFalse(np.isnan(result['momentum_5'].iloc[5]))

    # ===== 波动率因子 =====

    def test_volatility(self):
        result = FactorCalculator.volatility(self.df, window=10)
        self.assertIn('volatility_10', result.columns)
        self.assertTrue(result['volatility_10'].iloc[9:].notna().any())

    # ===== 换手率调整 =====

    def test_turnover_adj(self):
        # 没有 turnover 列时结果全是 NaN
        result = FactorCalculator.turnover_adj(self.df)
        self.assertIn('turnover_adj_5', result.columns)

    # ===== 量价趋势 =====

    def test_volume_price_trend(self):
        result = FactorCalculator.volume_price_trend(self.df)
        self.assertIn('vpt', result.columns)

    # ===== 布林带位置 =====

    def test_bollinger_position(self):
        result = FactorCalculator.bollinger_position(self.df, window=20)
        self.assertIn('bollinger_position', result.columns)
        # 布林带位置应在合理范围内
        valid = result['bollinger_position'].dropna()
        self.assertTrue((valid >= -0.5).all() and (valid <= 1.5).all())

    # ===== ATR =====

    def test_atr(self):
        result = FactorCalculator.atr(self.df, window=14)
        self.assertIn('atr_14', result.columns)
        self.assertTrue(result['atr_14'].iloc[13:].notna().any())

    # ===== 行业中性化 =====

    def test_industry_neutralize(self):
        df = self.df.copy()
        df['industry'] = df['stock_code'].apply(
            lambda x: 'bank' if '000001' in x else 'real_estate'
        )
        df['factor_value'] = np.random.randn(len(df))

        result = FactorCalculator.industry_neutralize(
            df, 'factor_value', 'industry'
        )
        self.assertIn('factor_value_neutralized', result.columns)

        # 中性化后每个行业均值应接近0
        for ind in ['bank', 'real_estate']:
            mean = result[result['industry'] == ind]['factor_value_neutralized'].mean()
            self.assertAlmostEqual(mean, 0, delta=1e-10)

    # ===== 市值中性化 =====

    def test_market_cap_neutralize(self):
        df = self.df.copy()
        df['market_cap'] = np.random.uniform(1e9, 1e12, len(df))
        df['factor_value'] = np.random.randn(len(df))

        result = FactorCalculator.market_cap_neutralize(
            df, 'factor_value', 'market_cap'
        )
        self.assertIn('factor_value_neutralized', result.columns)

    # ===== 多因子批量计算 =====

    def test_calculate_all(self):
        result = FactorCalculator.calculate_all(self.df)
        expected = [
            'momentum_5', 'momentum_20', 'volatility_20',
            'bollinger_position', 'vpt', 'atr_14'
        ]
        for col in expected:
            self.assertIn(col, result.columns)


if __name__ == '__main__':
    unittest.main()