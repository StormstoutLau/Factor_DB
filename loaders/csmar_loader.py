"""
CSMAR 数据加载器

从国泰安(CSMAR)数据库导出的 zip/xlsx 文件中加载因子数据到 Factor_DB 数据库。

CSMAR 数据格式特点：
    - xlsx 文件，前2行为元数据（英文/中文描述）
    - 第3行起为数据
    - 标准列名: Stkcd(股票代码), Trddt(交易日期), Markettype(市场类型)
    - 其他列根据具体数据表变化
"""

from __future__ import annotations

import logging
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from core.connection import DuckDBConnection
from loaders.base import BaseLoader, LoaderConfig
from utils.code_utils import normalize_stock_code

logger = logging.getLogger(__name__)

# CSMAR 列名映射
CSMAR_COLUMN_MAP = {
    'Stkcd': 'stock_code',
    'Trddt': 'trade_date',
    'Trdwnt': 'trade_date',       # 周度日期
    'Trmnt': 'trade_date',        # 月度日期
    'Trdynt': 'trade_date',       # 年度日期
    'Markettype': 'market_type',
    'MarketType': 'market_type',
    'MarkettypeID': 'market_type',
    'Status': 'status',
}

# 非因子值列（不导入 factor_data 的列）
NON_FACTOR_COLS = {
    'stock_code', 'trade_date', 'market_type', 'status',
    '证券代码', '交易日期', '市场类型', '交易状态',
}


class CSMARLoader(BaseLoader):
    """CSMAR 数据加载器

    支持从 zip 包或 xlsx 文件导入 CSMAR 格式的因子数据。

    Example:
        config = LoaderConfig(db_path='factor_db.duckdb')
        loader = CSMARLoader(config)

        # 加载单个 zip
        loader.load_zip(Path('动量因子表.zip'))

        # 加载整个因子目录
        loader.load_directory(Path(r'E:\因子研究'))
    """

    def __init__(self, config: Optional[LoaderConfig] = None):
        super().__init__(config)
        self._category_map: dict[str, str] = {}  # 因子名 → 分类

    def load(self, data_path: Path) -> int:
        """加载 CSMAR 数据

        Args:
            data_path: zip 文件或目录路径

        Returns:
            加载的记录数
        """
        if data_path.is_file() and data_path.suffix == '.zip':
            return self.load_zip(data_path)
        elif data_path.is_dir():
            return self.load_directory(data_path)
        else:
            logger.error(f"不支持的路径: {data_path}")
            return 0

    def load_zip(self, zip_path: Path, factor_category: str = '') -> int:
        """加载单个 zip 文件中的所有 xlsx

        Args:
            zip_path: zip 文件路径
            factor_category: 因子分类标签

        Returns:
            加载的记录数
        """
        logger.info(f"加载 zip: {zip_path.name}")

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                xlsx_files = [f for f in zf.namelist() if f.endswith('.xlsx')]

                if not xlsx_files:
                    logger.warning(f"zip 中无 xlsx 文件: {zip_path.name}")
                    return 0

                total = 0
                with tempfile.TemporaryDirectory() as tmpdir:
                    for xlsx_name in xlsx_files:
                        zf.extract(xlsx_name, tmpdir)
                        xlsx_path = Path(tmpdir) / xlsx_name
                        factor_name = self._extract_factor_name(xlsx_name)
                        count = self.load_xlsx(xlsx_path, factor_name, factor_category)
                        total += count

                logger.info(f"zip 导入完成: {zip_path.name} ({total:,} 条)")
                return total

        except Exception as e:
            logger.error(f"加载 zip 失败 {zip_path}: {e}")
            return 0

    def load_xlsx(
        self,
        xlsx_path: Path,
        factor_name: str = '',
        factor_category: str = '',
    ) -> int:
        """加载单个 CSMAR xlsx 文件

        Args:
            xlsx_path: xlsx 文件路径
            factor_name: 因子名称（从文件名提取）
            factor_category: 因子分类

        Returns:
            加载的记录数
        """
        if not factor_name:
            factor_name = xlsx_path.stem

        try:
            # 读取 xlsx（CSMAR 格式：前2行为元数据，跳过）
            df = pd.read_excel(xlsx_path, engine='openpyxl')

            if df.empty:
                logger.warning(f"xlsx 为空: {xlsx_path.name}")
                return 0

            # 跳过元数据行（前2行）
            df = self._clean_csmar_dataframe(df)

            if df.empty:
                logger.warning(f"xlsx 无有效数据: {xlsx_path.name}")
                return 0

            # 标准化列名
            df = self._normalize_columns(df)

            # 标准化股票代码
            if 'stock_code' in df.columns:
                df['stock_code'] = df['stock_code'].apply(
                    lambda x: normalize_stock_code(x) if pd.notna(x) else ''
                )
                df = df[df['stock_code'] != '']

            # 标准化日期
            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce').dt.date
                df = df.dropna(subset=['trade_date'])

            # 导入数据
            if 'stock_code' in df.columns and 'trade_date' in df.columns:
                return self._import_factor_data(df, factor_name, factor_category)
            else:
                # 市场级因子（无 stock_code），如 Fama-French
                return self._import_market_factor(df, factor_name, factor_category)

        except Exception as e:
            logger.error(f"加载 xlsx 失败 {xlsx_path.name}: {e}")
            return 0

    def load_directory(self, data_dir: Path) -> int:
        """递归加载目录下所有 CSMAR 数据

        Args:
            data_dir: CSMAR 数据根目录

        Returns:
            加载的总记录数
        """
        logger.info(f"扫描 CSMAR 数据目录: {data_dir}")

        # 收集所有 zip 文件
        zip_files = list(data_dir.rglob('*.zip'))
        logger.info(f"发现 {len(zip_files)} 个 zip 文件")

        total = 0
        for zip_path in zip_files:
            # 从路径中提取分类
            category = self._extract_category(zip_path, data_dir)
            count = self.load_zip(zip_path, category)
            total += count

        logger.info(f"CSMAR 目录导入完成: {total:,} 条总记录")
        return total

    def validate(self, data: any) -> bool:
        """验证 CSMAR 数据格式"""
        if not isinstance(data, pd.DataFrame):
            return False
        if data.empty:
            return False
        return True

    # ============================================================
    # 内部方法
    # ============================================================

    def _clean_csmar_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """清理 CSMAR DataFrame（跳过元数据行）

        CSMAR xlsx 格式：
            Row 0: 英文列名 (Stkcd, Trddt, ...)
            Row 1: 中文列名 (证券代码, 交易日期, ...)
            Row 2+: 数据

        Args:
            df: 原始 DataFrame

        Returns:
            清理后的 DataFrame
        """
        if len(df) < 3:
            return pd.DataFrame()

        # 第一行作为列名
        new_columns = [str(c).strip() for c in df.iloc[0].values]
        df = df.iloc[2:].copy()  # 跳过前2行元数据
        df.columns = new_columns
        df = df.reset_index(drop=True)

        return df

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化 CSMAR 列名

        Args:
            df: 原始 DataFrame

        Returns:
            列名标准化后的 DataFrame
        """
        rename_map = {}
        for col in df.columns:
            # 去除空格
            clean_col = str(col).strip()
            if clean_col in CSMAR_COLUMN_MAP:
                rename_map[col] = CSMAR_COLUMN_MAP[clean_col]

        if rename_map:
            df = df.rename(columns=rename_map)

        return df

    def _import_factor_data(
        self,
        df: pd.DataFrame,
        factor_name: str,
        factor_category: str = '',
    ) -> int:
        """导入个股因子数据到 factor_data 表

        Args:
            df: 标准化后的 DataFrame
            factor_name: 因子名称
            factor_category: 因子分类

        Returns:
            导入记录数
        """
        # 识别因子值列（排除 stock_code, trade_date, market_type, status）
        value_cols = [c for c in df.columns if c not in NON_FACTOR_COLS]

        if not value_cols:
            logger.warning(f"未找到因子值列: {factor_name}")
            return 0

        total = 0
        for val_col in value_cols:
            # 构建导入数据
            sub_df = df[['stock_code', 'trade_date', val_col]].copy()
            sub_df = sub_df.rename(columns={val_col: 'factor_value'})

            # 因子名：如果有多列，用 factor_name_col 格式
            if len(value_cols) > 1:
                full_factor_name = f"{factor_name}_{val_col}"
            else:
                full_factor_name = factor_name

            sub_df['factor_name'] = full_factor_name

            # 转换为数值
            sub_df['factor_value'] = pd.to_numeric(sub_df['factor_value'], errors='coerce')
            sub_df = sub_df.dropna(subset=['factor_value'])

            if sub_df.empty:
                continue

            try:
                with self.conn as conn:
                    conn.register('temp_csmar', sub_df[['trade_date', 'stock_code', 'factor_name', 'factor_value']])
                    conn.execute('''
                        INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
                        SELECT trade_date, stock_code, factor_name, factor_value
                        FROM temp_csmar
                    ''')
                    conn.unregister('temp_csmar')

                count = len(sub_df)
                total += count
                logger.debug(f"  {full_factor_name}: {count:,} 条")

            except Exception as e:
                logger.error(f"导入因子失败 {full_factor_name}: {e}")

        return total

    def _import_market_factor(
        self,
        df: pd.DataFrame,
        factor_name: str,
        factor_category: str = '',
    ) -> int:
        """导入市场级因子（无 stock_code，如 Fama-French 因子）

        市场级因子存储在 factor_data 表，stock_code 设为 'MARKET'。

        Args:
            df: 标准化后的 DataFrame
            factor_name: 因子名称
            factor_category: 因子分类

        Returns:
            导入记录数
        """
        value_cols = [c for c in df.columns if c not in NON_FACTOR_COLS and c != 'market_type']

        if not value_cols:
            return 0

        total = 0
        for val_col in value_cols:
            sub_df = df[['trade_date', val_col]].copy()
            sub_df = sub_df.rename(columns={val_col: 'factor_value'})
            sub_df['stock_code'] = 'MARKET'
            sub_df['factor_name'] = f"{factor_name}_{val_col}" if len(value_cols) > 1 else factor_name
            sub_df['factor_value'] = pd.to_numeric(sub_df['factor_value'], errors='coerce')
            sub_df = sub_df.dropna(subset=['factor_value'])

            if sub_df.empty:
                continue

            try:
                with self.conn as conn:
                    conn.register('temp_mkt', sub_df[['trade_date', 'stock_code', 'factor_name', 'factor_value']])
                    conn.execute('''
                        INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
                        SELECT trade_date, stock_code, factor_name, factor_value
                        FROM temp_mkt
                    ''')
                    conn.unregister('temp_mkt')

                count = len(sub_df)
                total += count
                logger.debug(f"  {val_col}: {count:,} 条")

            except Exception as e:
                logger.error(f"导入市场因子失败 {val_col}: {e}")

        return total

    def _extract_factor_name(self, filename: str) -> str:
        """从文件名提取因子名称

        Examples:
            LIQ_TOVER_D.xlsx → LIQ_TOVER_D
            STK_MKT_THRFACDAY.xlsx → STK_MKT_THRFACDAY

        Args:
            filename: 文件名

        Returns:
            因子名称
        """
        return Path(filename).stem

    def _extract_category(self, zip_path: Path, root_dir: Path) -> str:
        """从 zip 路径中提取因子分类

        Args:
            zip_path: zip 文件完整路径
            root_dir: 数据根目录

        Returns:
            分类路径（如 'Fama-French因子/三因子'）
        """
        try:
            rel = zip_path.parent.relative_to(root_dir)
            return str(rel).replace('\\', '/')
        except ValueError:
            return ''