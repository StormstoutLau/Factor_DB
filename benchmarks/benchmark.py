"""
性能基准测试

测试 DuckDB 查询性能，与 pandas 进行对比。
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd


def generate_test_data(n_dates: int, n_stocks: int) -> pd.DataFrame:
    """生成测试数据

    Args:
        n_dates: 日期数量
        n_stocks: 股票数量

    Returns:
        测试数据 DataFrame
    """
    dates = pd.date_range('2020-01-01', periods=n_dates, freq='B')
    stocks = [f"{i:06d}.SZ" if i % 2 == 0 else f"{i:06d}.SH" for i in range(n_stocks)]

    data = []
    for d in dates:
        for s in stocks:
            base_price = np.random.uniform(5, 100)
            data.append({
                'trade_date': d.date(),
                'stock_code': s,
                'open': base_price * np.random.uniform(0.98, 1.02),
                'high': base_price * np.random.uniform(1.0, 1.05),
                'low': base_price * np.random.uniform(0.95, 1.0),
                'close': base_price * np.random.uniform(0.98, 1.02),
                'volume': np.random.randint(1000, 1000000),
                'amount': np.random.uniform(10000, 10000000),
            })

    return pd.DataFrame(data)


def benchmark_duckdb_query(db_path: str, n_iterations: int = 10) -> dict:
    """测试 DuckDB 查询性能

    Args:
        db_path: 数据库路径
        n_iterations: 迭代次数

    Returns:
        性能指标字典
    """
    conn = duckdb.connect(db_path, read_only=True)

    # 测试 1: 单股票历史查询
    times = []
    for _ in range(n_iterations):
        start = time.perf_counter()
        conn.execute("""
            SELECT * FROM daily_prices
            WHERE stock_code = '000000.SZ'
            ORDER BY trade_date
        """).fetchdf()
        times.append(time.perf_counter() - start)
    single_stock_time = np.mean(times) * 1000  # ms

    # 测试 2: 全市场截面查询
    times = []
    for _ in range(n_iterations):
        start = time.perf_counter()
        conn.execute("""
            SELECT * FROM daily_prices
            WHERE trade_date = '2020-06-15'
            ORDER BY stock_code
        """).fetchdf()
        times.append(time.perf_counter() - start)
    cross_section_time = np.mean(times) * 1000

    # 测试 3: 价格矩阵查询
    times = []
    for _ in range(n_iterations):
        start = time.perf_counter()
        conn.execute("""
            SELECT trade_date, stock_code, close
            FROM daily_prices
            WHERE trade_date BETWEEN '2020-01-01' AND '2020-12-31'
        """).fetchdf()
        times.append(time.perf_counter() - start)
    matrix_time = np.mean(times) * 1000

    # 测试 4: 聚合统计
    times = []
    for _ in range(n_iterations):
        start = time.perf_counter()
        conn.execute("""
            SELECT
                stock_code,
                AVG(close) as avg_close,
                STDDEV(close) as std_close,
                MAX(close) as max_close,
                MIN(close) as min_close
            FROM daily_prices
            GROUP BY stock_code
        """).fetchdf()
        times.append(time.perf_counter() - start)
    agg_time = np.mean(times) * 1000

    conn.close()

    return {
        'single_stock_query_ms': single_stock_time,
        'cross_section_query_ms': cross_section_time,
        'matrix_query_ms': matrix_time,
        'aggregation_query_ms': agg_time,
    }


def benchmark_pandas_query(df: pd.DataFrame, n_iterations: int = 10) -> dict:
    """测试 pandas 查询性能

    Args:
        df: 测试数据 DataFrame
        n_iterations: 迭代次数

    Returns:
        性能指标字典
    """
    # 测试 1: 单股票历史查询
    times = []
    for _ in range(n_iterations):
        start = time.perf_counter()
        df[df['stock_code'] == '000000.SZ'].sort_values('trade_date')
        times.append(time.perf_counter() - start)
    single_stock_time = np.mean(times) * 1000

    # 测试 2: 全市场截面查询
    times = []
    for _ in range(n_iterations):
        start = time.perf_counter()
        df[df['trade_date'] == date(2020, 6, 15)].sort_values('stock_code')
        times.append(time.perf_counter() - start)
    cross_section_time = np.mean(times) * 1000

    # 测试 3: 价格矩阵查询
    times = []
    for _ in range(n_iterations):
        start = time.perf_counter()
        df[df['trade_date'].between(date(2020, 1, 1), date(2020, 12, 31))][
            ['trade_date', 'stock_code', 'close']
        ]
        times.append(time.perf_counter() - start)
    matrix_time = np.mean(times) * 1000

    # 测试 4: 聚合统计
    times = []
    for _ in range(n_iterations):
        start = time.perf_counter()
        df.groupby('stock_code')['close'].agg(['mean', 'std', 'max', 'min'])
        times.append(time.perf_counter() - start)
    agg_time = np.mean(times) * 1000

    return {
        'single_stock_query_ms': single_stock_time,
        'cross_section_query_ms': cross_section_time,
        'matrix_query_ms': matrix_time,
        'aggregation_query_ms': agg_time,
    }


def run_benchmark(n_dates: int = 252, n_stocks: int = 500):
    """运行基准测试

    Args:
        n_dates: 日期数量
        n_stocks: 股票数量
    """
    print(f"\n{'='*60}")
    print(f"Factor_DB 性能基准测试")
    print(f"数据规模: {n_dates} 天 × {n_stocks} 只股票 = {n_dates * n_stocks:,} 条记录")
    print(f"{'='*60}\n")

    # 生成测试数据
    print("生成测试数据...")
    df = generate_test_data(n_dates, n_stocks)

    # 创建临时数据库
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, 'benchmark.db')

    # 导入 DuckDB
    print("导入 DuckDB...")
    conn = duckdb.connect(db_path)
    conn.execute("""
        CREATE TABLE daily_prices (
            trade_date DATE,
            stock_code VARCHAR,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            amount DOUBLE
        )
    """)
    conn.register('temp_df', df)
    conn.execute("INSERT INTO daily_prices SELECT * FROM temp_df")
    conn.execute("CREATE INDEX idx_date ON daily_prices(trade_date)")
    conn.execute("CREATE INDEX idx_code ON daily_prices(stock_code)")
    conn.execute("CREATE INDEX idx_date_code ON daily_prices(trade_date, stock_code)")
    conn.unregister('temp_df')
    conn.close()

    # 运行 DuckDB 测试
    print("测试 DuckDB 查询性能...")
    duckdb_results = benchmark_duckdb_query(db_path)

    # 运行 pandas 测试
    print("测试 pandas 查询性能...")
    pandas_results = benchmark_pandas_query(df)

    # 打印结果
    print(f"\n{'='*60}")
    print(f"{'查询类型':<25} {'DuckDB (ms)':<15} {'pandas (ms)':<15} {'加速比':<10}")
    print(f"{'-'*60}")

    for key in duckdb_results:
        duckdb_time = duckdb_results[key]
        pandas_time = pandas_results[key]
        speedup = pandas_time / duckdb_time if duckdb_time > 0 else 0
        query_name = {
            'single_stock_query_ms': '单股票历史查询',
            'cross_section_query_ms': '全市场截面查询',
            'matrix_query_ms': '价格矩阵查询',
            'aggregation_query_ms': '聚合统计查询',
        }[key]
        print(f"{query_name:<20} {duckdb_time:>12.3f}    {pandas_time:>12.3f}    {speedup:>8.1f}x")

    print(f"{'='*60}\n")

    # 清理
    os.remove(db_path)
    os.rmdir(temp_dir)

    return duckdb_results, pandas_results


if __name__ == '__main__':
    # 小规模测试
    run_benchmark(n_dates=252, n_stocks=500)

    # 中规模测试
    run_benchmark(n_dates=756, n_stocks=1000)
