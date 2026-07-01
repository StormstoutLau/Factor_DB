"""
Phase 1: Ashare_data 全量数据导入脚本 (v2 - 性能优化版)

改进：
  - DuckDB UNPIVOT 替代 pandas melt（3-4x 提速）
  - 断点续传：跳过已导入的文件
  - 修复 OHLCV 字段名映射

用法:
    python scripts/import_phase1_ashare.py
    python scripts/import_phase1_ashare.py --db-path custom.duckdb --clean-ohlcv  # 清理错误导入的OHLCV
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.connection import DuckDBConnection
from core.schema import SchemaManager
from loaders.base import LoaderConfig
from loaders.daily_loader import DailyLoader
from utils.code_utils import normalize_stock_code

logger = logging.getLogger(__name__)

ASHARE_ROOT = Path(r'E:\Ashare_data')
MARKET_DATA = ASHARE_ROOT / 'market_data'
FACTOR_DIR = ASHARE_ROOT / '财务指标因子'

# 行情数据文件映射: {文件名 → 目标字段名}
OHLCV_FILES = {
    'stock_close.pkl': 'close',
    'stock_open.pkl': 'open',
    'stock_high.pkl': 'high',
    'stock_low.pkl': 'low',
    'stock_volume.pkl': 'volume',
    'stock_amount.pkl': 'amount',
}


def get_imported_factors(db_path: str) -> set[str]:
    """获取已导入的因子列表"""
    try:
        conn = DuckDBConnection(db_path, read_only=True)
        with conn as c:
            result = c.execute(
                'SELECT DISTINCT factor_name FROM factor_data'
            ).fetchall()
        conn.close()
        return {r[0] for r in result}
    except Exception:
        return set()


def get_imported_ohlcv_fields(db_path: str) -> set[str]:
    """获取 daily_prices 中已有数据的字段"""
    try:
        conn = DuckDBConnection(db_path, read_only=True)
        with conn as c:
            result = c.execute('''
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'daily_prices'
            ''').fetchall()
        existing_cols = {r[0] for r in result}
        conn.close()

        # 检查哪些列实际有数据
        fields_with_data = set()
        if 'close' in existing_cols:
            conn2 = DuckDBConnection(db_path, read_only=True)
            with conn2 as c:
                for field in ['close', 'open', 'high', 'low', 'volume', 'amount']:
                    if field in existing_cols:
                        cnt = c.execute(f'SELECT COUNT(*) FROM daily_prices WHERE {field} IS NOT NULL').fetchone()[0]
                        if cnt > 0:
                            fields_with_data.add(field)
            conn2.close()
        return fields_with_data
    except Exception:
        return set()


def init_database(db_path: str) -> bool:
    """初始化数据库"""
    logger.info("初始化数据库...")
    conn = DuckDBConnection(db_path)
    schema = SchemaManager(conn)
    schema.init_database()
    conn.close()
    return True


def import_ohlcv(db_path: str, skip_existing: bool = True) -> dict[str, int]:
    """导入 OHLCV 行情数据到 daily_prices"""
    logger.info("=" * 50)
    logger.info("Phase 1.1: 导入 OHLCV 行情数据")
    logger.info("=" * 50)

    config = LoaderConfig(db_path=db_path, show_progress=True)
    loader = DailyLoader(config)
    results = {}

    existing_fields = get_imported_ohlcv_fields(db_path) if skip_existing else set()

    for filename, field_name in OHLCV_FILES.items():
        if field_name in existing_fields:
            logger.info(f"跳过已导入: {field_name}")
            continue

        filepath = MARKET_DATA / filename
        if not filepath.exists():
            logger.warning(f"文件不存在: {filepath}")
            continue

        logger.info(f"导入 {filename} → daily_prices.{field_name}")
        count = loader.load(filepath, field_name=field_name)
        results[field_name] = count
        logger.info(f"  {field_name}: {count:,} 条记录")

    loader.conn.close()
    return results


def import_adj_factor(db_path: str) -> int:
    """导入复权因子到 daily_prices.adj_factor"""
    logger.info("=" * 50)
    logger.info("Phase 1.2: 导入复权因子 (adj_factor)")
    logger.info("=" * 50)

    filepath = MARKET_DATA / 'stock_adj.pkl'
    if not filepath.exists():
        logger.warning("复权因子文件不存在")
        return 0

    df = pd.read_pickle(filepath)
    logger.info(f"读取数据: {df.shape}")

    df = df.reset_index()
    date_col = df.columns[0]
    df = df.rename(columns={date_col: 'trade_date'})
    df_long = df.melt(id_vars=['trade_date'], var_name='stock_code', value_name='adj_factor')
    df_long['stock_code'] = df_long['stock_code'].apply(normalize_stock_code)
    df_long['trade_date'] = pd.to_datetime(df_long['trade_date']).dt.date
    df_long = df_long.dropna(subset=['adj_factor'])

    conn = DuckDBConnection(db_path, read_only=False)
    try:
        with conn as c:
            c.register('temp_adj', df_long)
            c.execute('''
                UPDATE daily_prices
                SET adj_factor = temp_adj.adj_factor
                FROM temp_adj
                WHERE daily_prices.trade_date = temp_adj.trade_date
                  AND daily_prices.stock_code = temp_adj.stock_code
            ''')
            c.unregister('temp_adj')
        logger.info(f"adj_factor 更新完成: {len(df_long):,} 条")
        return len(df_long)
    except Exception as e:
        logger.error(f"adj_factor 更新失败: {e}")
        return 0
    finally:
        conn.close()


def import_stock_info(db_path: str) -> dict[str, int]:
    """导入股票基本信息"""
    logger.info("=" * 50)
    logger.info("Phase 1.3: 导入股票基本信息")
    logger.info("=" * 50)

    conn = DuckDBConnection(db_path, read_only=False)
    results = {}

    # 上市日期
    list_date_path = MARKET_DATA / 'list_date_df.pkl'
    if list_date_path.exists():
        df = pd.read_pickle(list_date_path)
        df = df.reset_index()
        df.columns = ['stock_code', 'list_date']
        df['stock_code'] = df['stock_code'].apply(normalize_stock_code)
        df['list_date'] = pd.to_datetime(df['list_date']).dt.date

        with conn as c:
            c.register('temp_list', df)
            c.execute('INSERT OR REPLACE INTO stock_info (stock_code, list_date) SELECT stock_code, list_date FROM temp_list')
            c.unregister('temp_list')
        results['list_date'] = len(df)
        logger.info(f"  上市日期: {len(df):,} 条")

    # 股票名称
    name_path = MARKET_DATA / 'stock_name.pkl'
    if name_path.exists():
        df = pd.read_pickle(name_path)
        df = df.reset_index()
        if len(df.columns) >= 2:
            df = df.iloc[:, :2]
            df.columns = ['stock_code', 'stock_name']
            df['stock_code'] = df['stock_code'].apply(normalize_stock_code)
            with conn as c:
                c.register('temp_name', df)
                c.execute('UPDATE stock_info SET stock_name = temp_name.stock_name FROM temp_name WHERE stock_info.stock_code = temp_name.stock_code')
                c.execute('INSERT OR IGNORE INTO stock_info (stock_code, stock_name) SELECT stock_code, stock_name FROM temp_name')
                c.unregister('temp_name')
            results['stock_name'] = len(df)
            logger.info(f"  股票名称: {len(df):,} 条")

    # 行业分类
    ind_path = MARKET_DATA / 'stock_ind.pkl'
    if ind_path.exists():
        df = pd.read_pickle(ind_path)
        df = df.reset_index()
        date_col = df.columns[0]
        df = df.rename(columns={date_col: 'trade_date'})
        latest_row = df.iloc[-1:]
        df_melted = latest_row.melt(var_name='stock_code', value_name='industry')
        df_melted = df_melted[df_melted['stock_code'] != 'trade_date']
        df_melted['stock_code'] = df_melted['stock_code'].apply(normalize_stock_code)
        df_melted = df_melted.dropna(subset=['industry'])
        with conn as c:
            c.register('temp_ind', df_melted)
            c.execute('UPDATE stock_info SET industry = temp_ind.industry FROM temp_ind WHERE stock_info.stock_code = temp_ind.stock_code')
            c.execute('INSERT OR IGNORE INTO stock_info (stock_code, industry) SELECT stock_code, industry FROM temp_ind')
            c.unregister('temp_ind')
        results['industry'] = len(df_melted)
        logger.info(f"  行业分类: {len(df_melted):,} 条")

    conn.close()
    return results


def import_trade_calendar(db_path: str) -> int:
    """导入交易日历"""
    logger.info("=" * 50)
    logger.info("Phase 1.4: 导入交易日历")
    logger.info("=" * 50)

    filepath = MARKET_DATA / 'all_dates.pkl'
    if not filepath.exists():
        return 0

    data = pd.read_pickle(filepath)
    if isinstance(data, list):
        dates = pd.to_datetime(data)
    elif isinstance(data, pd.Index):
        dates = pd.to_datetime(data)
    else:
        dates = pd.to_datetime(pd.Series(data))

    df = pd.DataFrame({'trade_date': dates})
    df['trade_date'] = df['trade_date'].dt.date
    df['is_trading_day'] = True
    df['week_day'] = pd.to_datetime(df['trade_date']).dt.dayofweek
    df['is_month_end'] = False
    df['is_quarter_end'] = False
    df['is_year_end'] = False

    conn = DuckDBConnection(db_path, read_only=False)
    try:
        with conn as c:
            c.register('temp_cal', df)
            c.execute('INSERT OR REPLACE INTO trade_calendar (trade_date, is_trading_day, week_day, is_month_end, is_quarter_end, is_year_end) SELECT trade_date, is_trading_day, week_day, is_month_end, is_quarter_end, is_year_end FROM temp_cal')
            c.unregister('temp_cal')
        logger.info(f"交易日历: {len(df):,} 个交易日")
        return len(df)
    finally:
        conn.close()


def import_financial_factors(db_path: str, skip_existing: bool = True) -> dict[str, int]:
    """导入财务指标因子（支持断点续传，导入前删除索引以提升性能）"""
    logger.info("=" * 50)
    logger.info("Phase 1.5: 导入财务指标因子")
    logger.info("=" * 50)

    if not FACTOR_DIR.exists():
        return {}

    # 获取已导入的因子
    imported = get_imported_factors(db_path) if skip_existing else set()
    if imported:
        logger.info(f"已导入 {len(imported)} 个因子，将跳过")

    # 导入前：删除 factor_data 二级索引以加速写入
    conn_mgr = DuckDBConnection(db_path, read_only=False, close_on_exit=False)
    with conn_mgr as conn:
        conn.execute("SET memory_limit='16GB'")
        conn.execute("SET threads=4")
        conn.execute("SET preserve_insertion_order=false")
    schema = SchemaManager(conn_mgr)
    schema.drop_factor_indexes()
    logger.info("factor_data 二级索引已删除，memory_limit=16GB，开始批量导入")

    config = LoaderConfig(db_path=db_path, show_progress=True)
    loader = DailyLoader(config)
    results = {}

    factor_files = sorted(FACTOR_DIR.glob('*.pkl'))
    logger.info(f"发现 {len(factor_files)} 个财务因子文件")

    for filepath in factor_files:
        factor_name = filepath.stem

        if factor_name in imported:
            logger.info(f"跳过已导入: {factor_name}")
            continue

        logger.info(f"导入因子: {factor_name}")
        count = loader.load(filepath, field_name=factor_name)
        results[factor_name] = count

    loader.conn.close()

    # 导入后：重建索引（再次设置 memory_limit 确保生效）
    with conn_mgr as conn:
        conn.execute("SET memory_limit='16GB'")
        conn.execute("SET threads=4")
        conn.execute("SET preserve_insertion_order=false")
    schema.recreate_factor_indexes(safe_only=True)
    logger.info("factor_data 二级索引已重建 (safe_only)")
    conn_mgr.close()

    total = sum(results.values())
    logger.info(f"财务因子导入完成: {total:,} 条总记录")
    return results


def import_special_factors(db_path: str) -> dict[str, int]:
    """Phase 1.6: 导入特殊因子 (stock_return, stock_suspend, stock_df, barra_df_tl)

    数据格式分析:
      - stock_return: 宽表 DataFrame (1475×5688), 85.87% non-null, 直接用 UNPIVOT
      - stock_suspend: 宽表 DataFrame (1475×5681), dtype=object (None/'S'/'R'), 需映射 S→1/R→0
      - stock_df: 宽表 DataFrame (1475×5699), columns 含 .SH 后缀, normalize_stock_code 自动处理
      - barra_df_tl: Series of dicts (173 dates), 每个 dict 是 {stock_code: float_value}, 需转换为 long format
    """
    logger.info("=" * 50)
    logger.info("Phase 1.6: 导入特殊因子")
    logger.info("=" * 50)

    # 导入前：删除 factor_data 二级索引以加速写入
    conn_mgr = DuckDBConnection(db_path, read_only=False, close_on_exit=False)
    with conn_mgr as conn:
        conn.execute("SET memory_limit='16GB'")
        conn.execute("SET threads=4")
        conn.execute("SET preserve_insertion_order=false")
    schema = SchemaManager(conn_mgr)
    schema.drop_factor_indexes()
    logger.info("factor_data 二级索引已删除，memory_limit=16GB，开始批量导入")

    config = LoaderConfig(db_path=db_path, show_progress=False)
    loader = DailyLoader(config)
    results = {}

    # 1. stock_return — 宽表格式，直接使用 loader.load()
    return_path = MARKET_DATA / 'stock_return.pkl'
    if return_path.exists():
        logger.info("导入 stock_return → factor_data.stock_return")
        count = loader.load(return_path, field_name='stock_return')
        results['stock_return'] = count
        logger.info(f"  stock_return: {count:,} 条")

    # 2. stock_suspend — 宽表格式，dtype=object，需将 S/R 映射为数值
    suspend_path = MARKET_DATA / 'stock_suspend.pkl'
    if suspend_path.exists():
        logger.info("导入 stock_suspend → factor_data.stock_suspend (S→1, R→0)")
        df_suspend = pd.read_pickle(suspend_path)
        # 映射: 'S'(停牌)→1, 'R'(复牌)→0, None→NaN (UNPIVOT 时过滤)
        # pandas 2.1+ 用 .map(), 旧版用 .applymap()
        if hasattr(df_suspend, 'map'):
            df_suspend = df_suspend.map(lambda x: 1 if x == 'S' else (0 if x == 'R' else None))
        else:
            df_suspend = df_suspend.applymap(lambda x: 1 if x == 'S' else (0 if x == 'R' else None))
        count = loader.load_dataframe(df_suspend, field_name='stock_suspend')
        results['stock_suspend'] = count
        logger.info(f"  stock_suspend: {count:,} 条")

    # 3. stock_df — 宽表格式，columns 带 .SH 后缀，normalize_stock_code 自动剥离
    stock_df_path = MARKET_DATA / 'stock_df.pkl'
    if stock_df_path.exists():
        logger.info("导入 stock_df → factor_data.stock_df")
        count = loader.load(stock_df_path, field_name='stock_df')
        results['stock_df'] = count
        logger.info(f"  stock_df: {count:,} 条")

    # 4. barra_df_tl — 嵌套结构: date → {factor_name: {stock_code: value}}
    barra_path = MARKET_DATA / 'barra_df_tl.pkl'
    if barra_path.exists():
        logger.info("导入 barra_df_tl (嵌套: date → factor_name → {stock_code: value})")
        s_barra = pd.read_pickle(barra_path)
        # Keys: _id (skip), INDNAME (skip, string), BETA/MOMENTUM/SIZE/etc. (float factors)
        SKIP_KEYS = {'_id', 'INDNAME'}
        records = []
        for date, factor_dict in s_barra.items():
            for factor_name, stock_dict in factor_dict.items():
                if factor_name in SKIP_KEYS:
                    continue
                for stock_code, value in stock_dict.items():
                    records.append({
                        'trade_date': pd.to_datetime(date).date(),
                        'stock_code': normalize_stock_code(stock_code),
                        'factor_name': f'barra_tl_{factor_name}',
                        'factor_value': float(value),
                    })
        df_barra = pd.DataFrame(records)
        logger.info(f"  barra_tl 展开: {len(df_barra):,} 条记录")

        # 使用 conn_mgr（已配置 memory_limit）直接 INSERT
        with conn_mgr as conn:
            conn.register('_temp_barra', df_barra)
            conn.execute('''
                INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
                SELECT trade_date, stock_code, factor_name, factor_value
                FROM _temp_barra
            ''')
            conn.unregister('_temp_barra')
        results['barra_tl'] = len(df_barra)
        logger.info(f"  barra_tl: {len(df_barra):,} 条")

    loader.conn.close()

    # 导入后：重建索引
    with conn_mgr as conn:
        conn.execute("SET memory_limit='16GB'")
        conn.execute("SET threads=4")
        conn.execute("SET preserve_insertion_order=false")
    schema.recreate_factor_indexes(safe_only=True)
    logger.info("factor_data 二级索引已重建 (safe_only)")
    conn_mgr.close()

    total = sum(results.values())
    logger.info(f"特殊因子导入完成: {total:,} 条总记录")
    return results


def clean_ohlcv_from_factors(db_path: str):
    """清理错误导入到 factor_data 的 OHLCV 数据"""
    logger.info("清理 factor_data 中的 OHLCV 数据...")
    conn = DuckDBConnection(db_path, read_only=False)
    try:
        with conn as c:
            for field in ['stock_close', 'stock_open', 'stock_high', 'stock_low', 'stock_volume', 'stock_amount']:
                deleted = c.execute(f"DELETE FROM factor_data WHERE factor_name = '{field}'").fetchone()
                logger.info(f"  删除 {field}: {deleted} 条")
        logger.info("OHLCV 清理完成")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Phase 1: Ashare_data 全量数据导入 (v2)')
    parser.add_argument('--db-path', default='factor_db.duckdb', help='数据库路径')
    parser.add_argument('--skip-ohlcv', action='store_true', help='跳过 OHLCV')
    parser.add_argument('--skip-adj', action='store_true', help='跳过复权因子')
    parser.add_argument('--skip-info', action='store_true', help='跳过股票信息')
    parser.add_argument('--skip-calendar', action='store_true', help='跳过交易日历')
    parser.add_argument('--skip-factors', action='store_true', help='跳过财务因子')
    parser.add_argument('--skip-special', action='store_true', help='跳过特殊因子')
    parser.add_argument('--clean-ohlcv', action='store_true', help='清理 factor_data 中错误导入的 OHLCV')
    parser.add_argument('--no-resume', action='store_true', help='禁用断点续传（强制重新导入）')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )

    logger.info("=" * 60)
    logger.info("Phase 1: Ashare_data 全量数据导入 (v2)")
    logger.info(f"数据库: {args.db_path}")
    logger.info(f"数据源: {ASHARE_ROOT}")
    logger.info(f"断点续传: {'关闭' if args.no_resume else '开启'}")
    logger.info("=" * 60)

    init_database(args.db_path)

    # 清理错误导入的 OHLCV
    if args.clean_ohlcv:
        clean_ohlcv_from_factors(args.db_path)

    resume = not args.no_resume
    all_results = {}

    if not args.skip_ohlcv:
        all_results['ohlcv'] = import_ohlcv(args.db_path, skip_existing=resume)
    if not args.skip_adj:
        all_results['adj_factor'] = import_adj_factor(args.db_path)
    if not args.skip_info:
        all_results['stock_info'] = import_stock_info(args.db_path)
    if not args.skip_calendar:
        all_results['trade_calendar'] = import_trade_calendar(args.db_path)
    if not args.skip_factors:
        all_results['financial_factors'] = import_financial_factors(args.db_path, skip_existing=resume)
    if not args.skip_special:
        all_results['special_factors'] = import_special_factors(args.db_path)

    # 汇总
    logger.info("=" * 60)
    logger.info("Phase 1 导入完成汇总:")
    total = 0
    for category, result in all_results.items():
        if isinstance(result, dict):
            cat_total = sum(v for v in result.values() if isinstance(v, int))
            logger.info(f"  {category}: {len(result)} 项, {cat_total:,} 条")
            total += cat_total
        elif isinstance(result, int):
            logger.info(f"  {category}: {result:,} 条")
            total += result
    logger.info(f"  总计: {total:,} 条记录")


if __name__ == '__main__':
    main()