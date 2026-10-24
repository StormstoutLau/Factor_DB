"""
CSMAR 数据加载器

从国泰安(CSMAR)数据库导出的 zip/xlsx 文件中加载因子数据。

CSMAR 数据格式特点：
    - xlsx 文件，前 1–2 行为元数据（中文标签 / 单位），其后为数据
    - 标准列名: Stkcd(股票代码) / Symbol(组合明细中的股票代码) / Trddt(交易日期)
    - 大表按 1,000,000 行切分为多个 xlsx（文件名带尾号），需要按基名合并
    - 市场级/组合级表没有股票代码，但有 MarkettypeID / Quantile / StockClass /
      FormationPeriod / MarketValue / BookToMarket / Momentum / IsNotBSE 等维度列

写入策略：
    - ``history_only=False``（默认）：沿用双写（factor_history + factor_wide），
      适合少量、需要宽表快速截面查询的因子
    - ``history_only=True``：仅追加 factor_history，适合批量历史因子导入
      （CSMAR 因子列数量大、时间跨度超出宽表键范围，全部进宽表会导致宽表膨胀）
"""

from __future__ import annotations

import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

import pandas as pd

from Factor_DB.loaders.base import BaseLoader, LoaderConfig
from Factor_DB.utils.code_utils import normalize_stock_code
from Factor_DB.utils.sql_utils import quote_identifier, validate_identifier

logger = logging.getLogger(__name__)

# CSMAR 列名映射（统一到内部列名）
CSMAR_COLUMN_MAP = {
    # 股票实体
    'Stkcd': 'stock_code',
    'Symbol': 'stock_code',
    # 指数 / 行业实体
    'Indcd': 'index_code',
    'Nnindcd': 'industry_code',
    # 日期（日/周/月/年/区间末/停牌日）
    'Trddt': 'trade_date',
    'Trdwnt': 'trade_date',
    'Trdmnt': 'trade_date',
    'Trmnt': 'trade_date',
    'Trdynt': 'trade_date',
    'TradingDate': 'trade_date',
    'TradingWeek': 'trade_date',
    'TradingMonth': 'trade_date',
    'TradingYear': 'trade_date',
    'EndDate': 'trade_date',
    'Suspdate': 'trade_date',
    # 维度（用于因子名或市场级伪实体编码）
    'Markettype': 'market_type',
    'MarketType': 'market_type',
    'MarkettypeID': 'market_type',
    'Quantile': 'quantile',
    'StockClass': 'stock_class',
    'FormationPeriod': 'formation_period',
    'TestPeriod': 'test_period',
    'MarketValue': 'mv_group',
    'BooktoMarket': 'btm_group',
    'Momentum': 'mom_group',
    'IsNotBSE': 'is_not_bse',
    'Portfolios': 'portfolios',
    'Sample': 'sample',
    'ST': 'st_flag',
    'Finance': 'finance_flag',
    'ReturnType': 'return_type',
    'IsMainboard': 'is_mainboard',
    'Alpha': 'alpha_level',
    # 非因子列
    'Status': 'status',
    'Shortname': 'shortname',
    'ShortName': 'shortname',
    'Opndt': 'open_date',
    'Clsdt': 'close_date',
    'LastDate': 'last_date',
    'StartD': 'start_date',
    'EndD': 'end_date',
    'Anondt': 'announce_date',
    'Annodt': 'announce_date',
    'PubliDate': 'publish_date',
    'FirstPubliDate': 'first_publish_date',
    'TestStart': 'test_start',
    'm1Date': 'm1_date',
    'mtDate': 'mt_date',
    'EndDateTS': 'end_date_ts',
    'YearTS': 'year_ts',
    'Exchange': 'exchange',
    'IndustryCode': 'industry_attr',
    'INDUSTRYNAME': 'industry_name',
    'ShareHolderName': 'holder_name',
    'Name': 'person_name',
    'PersonID': 'person_id',
    'PostID': 'post_id',
    'Post': 'post',
    'ShareholderNature': 'holder_nature',
    'CategoryCode': 'category_code',
    'SharesNatureID': 'shares_nature',
    'Class': 'class',
}

# 日期列 → 粒度（周表形如 1990-53，需要按“年-周”解析）
DATE_COLUMN_KINDS = {
    'Trddt': 'day',
    'TradingDate': 'day',
    'EndDate': 'day',
    'Suspdate': 'day',
    'Trdwnt': 'week',
    'TradingWeek': 'week',
    'Trdmnt': 'month',
    'Trmnt': 'month',
    'TradingMonth': 'month',
    'Trdynt': 'year',
    'TradingYear': 'year',
}

# 维度列 → 编码前缀（顺序固定，保证命名可复现）
DIMENSION_COLUMNS = {
    'market_type': 'MT',
    'quantile': 'Q',
    'stock_class': 'C',
    'formation_period': 'F',
    'test_period': 'T',
    'return_type': 'RT',
    'sample': 'SMP',
    'st_flag': 'ST',
    'finance_flag': 'FIN',
    'is_mainboard': 'MB',
    'portfolios': 'P',
    'alpha_level': 'ALPHA',
    'mv_group': 'MV',
    'btm_group': 'BTM',
    'mom_group': 'MOM',
    'is_not_bse': 'NB',
}

# 表级额外维度：仅对指定基名生效（列名 → 命名标签）
# HF_VPIN：同一股票每日有 N=50 / N=8 两种篮子数，若不作维度会按 keep='last'
# 去重丢弃一半行
STEM_DIMENSIONS = {
    'HF_VPIN': {'N': 'N'},
}

# 维度列未激活时的原始列名（用于因子值命名，如 DGTW 股票分配表）
DIMENSION_DISPLAY_NAMES = {
    'mv_group': 'MarketValue',
    'btm_group': 'BooktoMarket',
    'mom_group': 'Momentum',
    'is_not_bse': 'IsNotBSE',
}

# DGTW 专用维度：仅当同时出现 ≥2 个、且表中还有其他数值列时生效，
# 避免把 DGTW 股票分配表的分组标识误当作维度而丢失全部因子值
DGTW_DIMENSIONS = {'mv_group', 'btm_group', 'mom_group', 'is_not_bse'}

# 非因子值列（不参与 UNPIVOT）
NON_FACTOR_COLS = {
    'stock_code', 'index_code', 'industry_code', 'trade_date',
    'status', 'shortname', 'open_date', 'close_date', 'last_date',
    'start_date', 'end_date', 'announce_date', 'publish_date',
    'first_publish_date', 'test_start', 'm1_date', 'mt_date',
    'end_date_ts', 'year_ts', 'exchange', 'industry_attr', 'industry_name',
    'holder_name', 'person_name', 'person_id', 'post_id', 'post',
    'holder_nature', 'category_code', 'shares_nature', 'class',
    'market_type', 'quantile', 'stock_class', 'formation_period',
    'test_period', 'return_type', 'sample', 'st_flag', 'finance_flag',
    'is_mainboard', 'portfolios', 'alpha_level', 'mv_group', 'btm_group',
    'mom_group', 'is_not_bse',
    # 中文列名兜底
    '证券代码', '交易日期', '交易状态', '市场类型', '分位数', '股票样本类别',
    '形成期', '检验期', '股票代码', '股票简称', '开盘日', '收盘日',
}

# 明细/登记类表：同一 (股票, 日期) 存在多行（股东、高管逐人明细），
# 无截面因子语义，跳过
SKIP_STEMS = {
    'BF_HOLDER', 'BF_HOLDER1', 'BF_HOLDER2',
    'BF_EXSHARE', 'BF_EXSHARE1',
}

# 实体/市场级伪代码
MARKET_ENTITY = 'MKT'
INDEX_PREFIX = 'IDX'
INDUSTRY_PREFIX = 'IND'

# 导入日志表（用于断点续跑）
IMPORT_LOG_TABLE = 'csmar_import_log'


class CSMARLoader(BaseLoader):
    """CSMAR 数据加载器

    支持从 zip 包、xlsx 文件或目录导入 CSMAR 因子数据。

    Example:
        config = LoaderConfig(db_path='factor_db.duckdb')
        loader = CSMARLoader(config, history_only=True)

        # 加载单个 zip
        loader.load_zip(Path('动量因子表.zip'))

        # 加载整个因子目录
        loader.load_directory(Path(r'E:\因子研究'))
    """

    def __init__(self, config: Optional[LoaderConfig] = None,
                 history_only: bool = False):
        """
        Args:
            config: 加载器配置
            history_only: 为 True 时仅写 factor_history（批量导入推荐）
        """
        super().__init__(config)
        self.history_only = history_only
        self._category_map: dict[str, str] = {}  # 因子名 → 分类
        self._done_sources: Optional[set[str]] = None

    def load(self, data_path: Path) -> int:
        """加载 CSMAR 数据

        Args:
            data_path: zip 文件、xlsx 文件或目录路径

        Returns:
            加载的记录数
        """
        if data_path.is_file() and data_path.suffix == '.zip':
            return self.load_zip(data_path)
        elif data_path.is_file() and data_path.suffix == '.xlsx':
            return self.load_xlsx(data_path, source_key=str(data_path))
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
                xlsx_files = [f for f in zf.namelist() if f.lower().endswith('.xlsx')]

                if not xlsx_files:
                    logger.warning(f"zip 中无 xlsx 文件: {zip_path.name}")
                    return 0

                total = 0
                with tempfile.TemporaryDirectory() as tmpdir:
                    for xlsx_name in xlsx_files:
                        source_key = f'{zip_path.name}/{xlsx_name}'
                        if self._is_done(source_key):
                            continue
                        zf.extract(xlsx_name, tmpdir)
                        xlsx_path = Path(tmpdir) / xlsx_name
                        count = self.load_xlsx(
                            xlsx_path, factor_category=factor_category,
                            source_key=source_key,
                        )
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
        source_key: Optional[str] = None,
    ) -> int:
        """加载单个 CSMAR xlsx 文件

        Args:
            xlsx_path: xlsx 文件路径
            factor_name: 因子基名（默认取文件名，并合并尾号为数据分片）
            factor_category: 因子分类
            source_key: 断点续跑标识（zip 名/xlsx 名 或 绝对路径）

        Returns:
            加载的记录数
        """
        if not factor_name:
            factor_name = xlsx_path.stem
        base_name = self._base_factor_name(factor_name)

        if base_name in SKIP_STEMS:
            logger.info(f"跳过明细/登记类表: {base_name} ({xlsx_path.name})")
            if source_key:
                self._log_source(source_key, base_name, factor_category, 0)
            return 0

        try:
            df = self._read_excel(xlsx_path)
            if df.empty:
                logger.warning(f"xlsx 为空: {xlsx_path.name}")
                return 0

            count = self._import_frame(
                df, base_name, factor_category, source_key=source_key
            )
            logger.debug(f"  {base_name}: {count:,} 条")
            return count

        except Exception as e:
            logger.error(f"加载 xlsx 失败 {xlsx_path.name}: {e}", exc_info=True)
            return 0

    def load_directory(self, data_dir: Path) -> int:
        """递归加载目录下所有 CSMAR 数据

        Args:
            data_dir: CSMAR 数据根目录

        Returns:
            加载的总记录数
        """
        logger.info(f"扫描 CSMAR 数据目录: {data_dir}")
        self._apply_settings(self.conn)
        self._ensure_import_log()

        zip_files = sorted(data_dir.rglob('*.zip'))
        loose_xlsx = sorted(data_dir.rglob('*.xlsx'))
        logger.info(f"发现 {len(zip_files)} 个 zip 文件, {len(loose_xlsx)} 个散装 xlsx")

        # zip 内已出现的表名，避免重复导入散装副本
        zip_stems: set[str] = set()
        for zip_path in zip_files:
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    zip_stems.update(
                        Path(n).stem for n in zf.namelist()
                        if n.lower().endswith('.xlsx')
                    )
            except Exception:
                continue

        total = 0
        for i, zip_path in enumerate(zip_files, 1):
            category = self._extract_category(zip_path, data_dir)
            count = self.load_zip(zip_path, category)
            total += count
            logger.info(f"[{i}/{len(zip_files)}] {zip_path.name}: {count:,} 条, 累计 {total:,}")

        for xlsx_path in loose_xlsx:
            if xlsx_path.stem in zip_stems:
                logger.debug(f"跳过 zip 内已有副本: {xlsx_path.name}")
                continue
            if self._is_done(str(xlsx_path)):
                continue
            category = self._extract_category(xlsx_path, data_dir)
            count = self.load_xlsx(xlsx_path, factor_category=category,
                                   source_key=str(xlsx_path))
            total += count
            logger.info(f"[xlsx] {xlsx_path.name}: {count:,} 条, 累计 {total:,}")

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

    @staticmethod
    def _read_excel(xlsx_path: Path) -> pd.DataFrame:
        """读取 xlsx（优先 calamine，回退 openpyxl）"""
        try:
            return pd.read_excel(xlsx_path, engine='calamine')
        except Exception:
            logger.debug(f"calamine 读取失败，回退 openpyxl: {xlsx_path.name}")
            return pd.read_excel(xlsx_path, engine='openpyxl')

    @staticmethod
    def _base_factor_name(stem: str) -> str:
        """合并 1,000,000 行切分产生的尾号分片

        Examples:
            HF_BSImbalance1 → HF_BSImbalance
            LIQ_AMIHUD_D2   → LIQ_AMIHUD_D
        """
        name = str(stem).strip()
        return re.sub(r'\d+$', '', name) or name

    def _clean_csmar_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """清理 CSMAR DataFrame（跳过元数据行）

        CSMAR xlsx 格式：
            pandas header: 英文列名 (Stkcd, Trddt, TradingDate, ...)
            Row 0: 中文标签 (证券代码, 交易日期, ...)
            Row 1: 单位 (没有单位 / 元 / 月 / ...)
            Row 2+: 数据

        pandas 已把英文列名解析为 columns，这里只跳过前 1–2 行元数据，
        保留英文列名供 ``_normalize_columns`` 映射。
        """
        if df.empty:
            return df

        def _is_meta_row(row) -> bool:
            values = [v for v in row.values if pd.notna(v)]
            if not values:
                return False
            if any(v == '没有单位' for v in values):
                return True
            chinese = sum(
                1 for v in values
                if isinstance(v, str) and re.search(r'[\u4e00-\u9fff]', v)
            )
            return chinese >= max(2, len(values) // 2)

        drop_rows = 0
        for i in range(min(2, len(df))):
            if _is_meta_row(df.iloc[i]):
                drop_rows = i + 1
            else:
                break

        df = df.iloc[drop_rows:].copy()
        df.columns = [str(c).strip().lstrip('\ufeff') for c in df.columns]
        return df.reset_index(drop=True)

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化 CSMAR 列名（含重名去重）"""
        rename_map = {}
        self._date_kind = 'day'
        date_kind_set = False
        for col in df.columns:
            clean_col = str(col).strip()
            if clean_col in CSMAR_COLUMN_MAP:
                target = CSMAR_COLUMN_MAP[clean_col]
                rename_map[col] = target
                if (target == 'trade_date' and not date_kind_set
                        and clean_col in DATE_COLUMN_KINDS):
                    self._date_kind = DATE_COLUMN_KINDS[clean_col]
                    date_kind_set = True

        if rename_map:
            df = df.rename(columns=rename_map)

        # 多个源列映射到同一内部列名时仅保留首个（例如多种日期列并存）
        if df.columns.duplicated().any():
            dupes = df.columns[df.columns.duplicated()].unique().tolist()
            logger.warning(f"列名映射冲突，保留首列: {dupes}")
            df = df.loc[:, ~df.columns.duplicated()]

        return df

    def _parse_dates(self, series: pd.Series) -> pd.Series:
        """解析 CSMAR 日期列（日/周/月/年格式）"""
        if pd.api.types.is_numeric_dtype(series):
            series = series.astype('Int64').astype(str)

        kind = getattr(self, '_date_kind', 'day')

        formats = ('%Y-%m-%d', '%Y-%m', '%Y')
        if kind != 'week':
            for fmt in formats:
                parsed = pd.to_datetime(series, format=fmt, errors='coerce')
                if parsed.notna().mean() >= 0.95:
                    return parsed
        else:
            # 周表优先按具体日期解析
            parsed = pd.to_datetime(series, format='%Y-%m-%d', errors='coerce')
            if parsed.notna().mean() >= 0.95:
                return parsed

            # 形如 1990-53（%W 不能直接用于 pandas，手工换算）
            parts = series.astype(str).str.extract(r'^\s*(\d{4})[-/](\d{1,2})\s*$')
            year = pd.to_numeric(parts[0], errors='coerce')
            week = pd.to_numeric(parts[1], errors='coerce')
            base = pd.to_datetime(year.astype('Int64').astype(str) + '-01-01',
                                  errors='coerce')
            parsed = base + pd.to_timedelta((week - 1) * 7, unit='D')
            if parsed.notna().mean() >= 0.95:
                return parsed

            for fmt in formats[1:]:
                parsed = pd.to_datetime(series, format=fmt, errors='coerce')
                if parsed.notna().mean() >= 0.95:
                    return parsed

        return pd.to_datetime(series, errors='coerce', format='mixed')

    @staticmethod
    def _sanitize_token(value) -> str:
        """把维度取值转为标识符安全片段（30% → 30, 1.0 → 1）"""
        if isinstance(value, bool):
            return '1' if value else '0'
        try:
            num = float(value)
            if num.is_integer():
                return str(int(num))
        except (TypeError, ValueError):
            pass
        token = re.sub(r'[^0-9A-Za-z]', '', str(value))
        return token or 'NA'

    @staticmethod
    def _sanitize_column(col: str) -> str:
        """把值列名转为标识符安全片段（Mkt-RF → Mkt_RF）"""
        token = re.sub(r'[^0-9A-Za-z_]', '_', str(col).strip())
        token = re.sub(r'_+', '_', token).strip('_')
        if not token:
            token = 'V'
        if token[0].isdigit():
            token = f'V_{token}'
        return token

    def _resolve_entity(self, df: pd.DataFrame) -> tuple[pd.Series, str]:
        """确定实体列，返回 (实体编码, 实体类型)"""
        if 'stock_code' in df.columns:
            codes = df['stock_code'].map(normalize_stock_code)
            mask = codes.str.fullmatch(r'\d{6}')
            return codes.where(mask), 'stock'

        if 'index_code' in df.columns:
            raw = df['index_code'].map(normalize_stock_code)
            codes = INDEX_PREFIX + raw
            mask = raw.str.fullmatch(r'\d{6}')
            return codes.where(mask), 'index'

        if 'industry_code' in df.columns:
            tokens = df['industry_code'].map(self._sanitize_token)
            codes = INDUSTRY_PREFIX + tokens
            mask = df['industry_code'].notna()
            return codes.where(mask), 'industry'

        return pd.Series(MARKET_ENTITY, index=df.index), 'market'

    def _active_dimensions(self, df: pd.DataFrame,
                           entity: pd.Series,
                           base_name: str = '') -> list[str]:
        """确定本次导入生效的维度列（顺序固定）

        仅保留真正用于拆分同一 (日期, 实体) 多行的维度，
        如市场类型、分位数、形成期；像 MarketType 这种个股属性列不进入命名。
        表级维度（``STEM_DIMENSIONS``，如 HF_VPIN 的 N）仅在对应基名下参与。
        """
        candidates = [c for c in DIMENSION_COLUMNS if c in df.columns]
        stem_dims = STEM_DIMENSIONS.get(base_name, {})
        candidates += [
            c for c in stem_dims if c in df.columns and c not in candidates
        ]
        dim_names = set(DIMENSION_COLUMNS) | set(stem_dims)

        # DGTW 维度防误判：需同时出现 ≥2 个且存在其他数值列
        dgtw_present = [c for c in candidates if c in DGTW_DIMENSIONS]
        if len(dgtw_present) >= 2:
            others = [
                c for c in df.columns
                if c not in NON_FACTOR_COLS and c not in dim_names
            ]
            has_other_numeric = any(
                pd.to_numeric(df[c], errors='coerce').notna().any()
                for c in others
            )
            if not has_other_numeric:
                candidates = [c for c in candidates if c not in DGTW_DIMENSIONS]
        else:
            candidates = [c for c in candidates if c not in DGTW_DIMENSIONS]

        keys = df['trade_date'].astype(str) + '|' + entity.astype(str)
        needed = []
        for col in candidates:
            grouped = df.assign(_key=keys).groupby('_key', sort=False)[col]
            if grouped.nunique(dropna=True).gt(1).any():
                needed.append(col)
        return needed

    def _build_dim_suffix(self, df: pd.DataFrame,
                          dims: list[str],
                          base_name: str = '') -> pd.Series:
        """构造维度后缀，如 _MT_P9701_Q30_C0_F12"""
        labels = {**DIMENSION_COLUMNS, **STEM_DIMENSIONS.get(base_name, {})}
        suffix = pd.Series('', index=df.index, dtype=object)
        for col in dims:
            label = labels[col]
            tokens = df[col].map(
                lambda v: self._sanitize_token(v) if pd.notna(v) else 'NA'
            )
            suffix = suffix + '_' + label + '_' + tokens.astype(str)
        return suffix

    def _import_frame(
        self,
        raw_df: pd.DataFrame,
        base_name: str,
        factor_category: str = '',
        source_key: Optional[str] = None,
        is_market: Optional[bool] = None,
    ) -> int:
        """清洗 + 命名 + 写入（统一入口）

        Args:
            raw_df: 原始 DataFrame
            base_name: 因子基名（已合并尾号分片）
            factor_category: 分类
            source_key: 断点续跑标识
            is_market: 强制指定市场级（None 时按实体列判断）

        Returns:
            写入记录数
        """
        df = self._clean_csmar_dataframe(raw_df)
        if df.empty:
            return 0
        df = self._normalize_columns(df)

        if 'trade_date' not in df.columns:
            logger.warning(f"缺少日期列，跳过: {base_name}")
            return 0

        # 日期
        df['trade_date'] = self._parse_dates(df['trade_date'])
        df = df.dropna(subset=['trade_date'])

        # 实体
        entity, entity_type = self._resolve_entity(df)
        if is_market is True:
            entity = pd.Series(MARKET_ENTITY, index=df.index)
            entity_type = 'market'
        df = df.assign(stock_code=entity)
        df = df[df['stock_code'].notna()]

        if df.empty:
            logger.warning(f"无有效实体/日期，跳过: {base_name}")
            return 0

        # 维度后缀
        dims = self._active_dimensions(df, df['stock_code'], base_name)
        dim_suffix = (
            self._build_dim_suffix(df, dims, base_name) if dims
            else pd.Series('', index=df.index, dtype=object)
        )

        # 值列（DGTW 分组列在未激活为维度时按因子值导入）
        deactivated_dgtw = set(DGTW_DIMENSIONS) - set(dims)
        value_cols = [
            c for c in df.columns
            if (c not in NON_FACTOR_COLS or c in deactivated_dgtw)
            and c not in dims
        ]

        numeric_cols = []
        for col in value_cols:
            coerced = pd.to_numeric(df[col], errors='coerce')
            if coerced.notna().any():
                df[col] = coerced
                numeric_cols.append(col)

        if not numeric_cols:
            logger.warning(f"未找到有效数值列，跳过: {base_name}")
            return 0

        work = df[['trade_date', 'stock_code', *numeric_cols]].copy()
        work['dim_suffix'] = dim_suffix

        dup_keys = ['trade_date', 'stock_code', 'dim_suffix']
        before = len(work)
        work = work.drop_duplicates(subset=dup_keys, keep='last')
        dropped = before - len(work)
        if dropped:
            ratio = dropped / max(before, 1)
            level = logger.warning if ratio > 0.2 else logger.debug
            level(f"{base_name}: 去重丢弃 {dropped:,} 行 ({ratio:.1%})")

        # 市场级因子：维度编码进伪实体；个股/指数/行业：维度编码进因子名
        dims_in_entity = entity_type == 'market' and bool(dims)
        dims_in_name = entity_type != 'market' and bool(dims)

        single = len(numeric_cols) == 1
        prefixes: dict[str, str] = {}
        used: set[str] = set()
        for col in numeric_cols:
            display = DIMENSION_DISPLAY_NAMES.get(col, col)
            prefix = base_name if single else (
                f'{base_name}_{self._sanitize_column(display)}'
            )
            if prefix in used:
                prefix = f'{prefix}_{self._sanitize_column(display)}'
            used.add(prefix)
            prefixes[col] = prefix

        with self.conn as conn:
            self._apply_settings(conn)
            conn.register('_csmar_import_tmp', work)
            try:
                if self.history_only:
                    total = self._write_all_columns_history(
                        conn, prefixes, dims_in_name, dims_in_entity,
                        base_name, factor_category,
                    )
                else:
                    total = 0
                    for col, prefix in prefixes.items():
                        total += self._write_value_column(
                            conn, col, prefix,
                            dims_in_name=dims_in_name,
                            dims_in_entity=dims_in_entity,
                            base_name=base_name,
                            category=factor_category,
                        )
            finally:
                conn.unregister('_csmar_import_tmp')

        if source_key:
            self._log_source(source_key, base_name, factor_category, total)

        logger.info(
            f"{base_name}: {total:,} 条, 实体={entity_type}, 维度={dims or '无'}"
        )
        return total

    def _write_all_columns_history(self, conn, prefixes: dict[str, str],
                                   dims_in_name: bool, dims_in_entity: bool,
                                   base_name: str, category: str) -> int:
        """单条 UNPIVOT 语句把整文件所有数值列追加到 factor_history

        相比逐列写入，整文件一次扫描、一次事务，显著减少 I/O 与提交开销。
        """
        if not prefixes:
            return 0

        cols_sql = ', '.join(quote_identifier(c) for c in prefixes)
        name_sql = (
            "m.factor_name || u.dim_suffix" if dims_in_name else 'm.factor_name'
        )
        entity_sql = (
            "u.stock_code || u.dim_suffix" if dims_in_entity else 'u.stock_code'
        )

        # 列名 → 因子名 映射（参数化构造，避免拼接注入）
        map_params: dict[str, object] = {}
        values = []
        for i, (col, prefix) in enumerate(prefixes.items()):
            values.append(f"($c{i}, $n{i})")
            map_params[f'c{i}'] = col
            map_params[f'n{i}'] = prefix

        conn.execute('BEGIN TRANSACTION')
        try:
            conn.execute(
                'CREATE OR REPLACE TEMP TABLE _csmar_name_map '
                '(col_name VARCHAR, factor_name VARCHAR)'
            )
            conn.execute(
                f"INSERT INTO _csmar_name_map VALUES {', '.join(values)}",
                map_params,
            )

            long_sql = f'''
                SELECT
                    CAST(u.trade_date AS DATE) AS trade_date,
                    {entity_sql} AS stock_code,
                    {name_sql} AS factor_name,
                    CAST(u.factor_value AS DOUBLE) AS factor_value
                FROM (
                    UNPIVOT _csmar_import_tmp
                    ON {cols_sql}
                    INTO NAME col_name VALUE factor_value
                ) u
                JOIN _csmar_name_map m ON m.col_name = u.col_name
                WHERE u.factor_value IS NOT NULL AND u.trade_date IS NOT NULL
            '''

            count = conn.execute(
                f'SELECT COUNT(*) FROM ({long_sql}) t'
            ).fetchone()[0]

            conn.execute(f'''
                INSERT INTO factor_history
                    (trade_date, stock_code, factor_name, factor_value, loaded_at)
                SELECT t.*, CURRENT_TIMESTAMP
                FROM ({long_sql}) t
            ''')

            conn.execute(f'''
                INSERT INTO factor_info
                    (factor_name, factor_type, description, category, update_time)
                SELECT DISTINCT
                    factor_name, 'csmar', $base, $category, CURRENT_TIMESTAMP
                FROM ({long_sql}) t
                ON CONFLICT (factor_name) DO UPDATE SET
                    factor_type = EXCLUDED.factor_type,
                    description = EXCLUDED.description,
                    category = EXCLUDED.category,
                    update_time = EXCLUDED.update_time
            ''', {'base': base_name, 'category': category})

            conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK')
            raise
        finally:
            conn.execute('DROP TABLE IF EXISTS _csmar_name_map')

        return int(count)

    def _write_value_column(self, conn, value_col: str, name_prefix: str,
                            dims_in_name: bool, dims_in_entity: bool,
                            base_name: str = '',
                            category: str = '') -> int:
        """把一列数值双写 factor_history + factor_wide（逐因子名）"""
        validate_identifier(value_col, 'CSMAR 值列')
        validate_identifier(name_prefix, 'CSMAR 因子名')

        col_sql = quote_identifier(value_col)
        name_sql = "$prefix || dim_suffix" if dims_in_name else '$prefix'
        entity_sql = "stock_code || dim_suffix" if dims_in_entity else 'stock_code'

        base_sql = f'''
            SELECT
                CAST(trade_date AS DATE) AS trade_date,
                {entity_sql} AS stock_code,
                {name_sql} AS factor_name,
                CAST({col_sql} AS DOUBLE) AS factor_value
            FROM _csmar_import_tmp
            WHERE {col_sql} IS NOT NULL AND trade_date IS NOT NULL
        '''

        # 按具体因子名逐个写入（维度可能产生多个因子名）
        conn.execute(f'CREATE OR REPLACE TEMP TABLE _csmar_long AS {base_sql}',
                     {'prefix': name_prefix})
        try:
            names = [
                row[0] for row in conn.execute(
                    'SELECT DISTINCT factor_name FROM _csmar_long ORDER BY 1'
                ).fetchall()
            ]
            count = 0
            for name in names:
                validate_identifier(name, 'CSMAR 因子名')
                conn.execute(
                    'CREATE OR REPLACE TEMP TABLE _csmar_one AS '
                    'SELECT trade_date, stock_code, factor_value '
                    'FROM _csmar_long WHERE factor_name = $name',
                    {'name': name},
                )
                try:
                    self._write_factor_from_temp(conn, '_csmar_one', name)
                    count += conn.execute(
                        'SELECT COUNT(*) FROM _csmar_one'
                    ).fetchone()[0]
                finally:
                    conn.execute('DROP TABLE IF EXISTS _csmar_one')
                self._register_factor_info(conn, name, base_name, category)
            return int(count)
        finally:
            conn.execute('DROP TABLE IF EXISTS _csmar_long')

    def _register_factor_info(self, conn, factor_name: str, description: str,
                              category: str) -> None:
        """登记单个因子元数据（factor_info）"""
        try:
            conn.execute('''
                INSERT INTO factor_info
                    (factor_name, factor_type, description, category, update_time)
                VALUES ($name, 'csmar', $description, $category, CURRENT_TIMESTAMP)
                ON CONFLICT (factor_name) DO UPDATE SET
                    factor_type = EXCLUDED.factor_type,
                    description = EXCLUDED.description,
                    category = EXCLUDED.category,
                    update_time = EXCLUDED.update_time
            ''', {
                'name': factor_name,
                'description': description,
                'category': category,
            })
        except Exception as e:
            logger.warning(f"登记 factor_info 失败 {factor_name}: {e}")

    # ---------- 断点续跑日志 ----------

    def _ensure_import_log(self) -> None:
        with self.conn as conn:
            conn.execute(f'''
                CREATE TABLE IF NOT EXISTS {IMPORT_LOG_TABLE} (
                    source_file VARCHAR PRIMARY KEY,
                    stem VARCHAR,
                    category VARCHAR,
                    rows BIGINT,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            self._done_sources = {
                row[0] for row in conn.execute(
                    f'SELECT source_file FROM {IMPORT_LOG_TABLE}'
                ).fetchall()
            }

    def _is_done(self, source_key: str) -> bool:
        if self._done_sources is None:
            self._ensure_import_log()
        return source_key in self._done_sources

    def _log_source(self, source_key: str, stem: str, category: str,
                    rows: int) -> None:
        if self._done_sources is None:
            self._ensure_import_log()
        with self.conn as conn:
            conn.execute(f'''
                INSERT INTO {IMPORT_LOG_TABLE}
                    (source_file, stem, category, rows, imported_at)
                VALUES ($source, $stem, $category, $rows, CURRENT_TIMESTAMP)
                ON CONFLICT (source_file) DO UPDATE SET
                    stem = EXCLUDED.stem,
                    category = EXCLUDED.category,
                    rows = EXCLUDED.rows,
                    imported_at = EXCLUDED.imported_at
            ''', {
                'source': source_key,
                'stem': stem,
                'category': category,
                'rows': int(rows),
            })
        self._done_sources.add(source_key)

    # ---------- 兼容旧调用 ----------

    def _import_factor_data(
        self,
        df: pd.DataFrame,
        factor_name: str,
        factor_category: str = '',
    ) -> int:
        """导入个股因子数据（兼容旧接口）"""
        return self._import_frame(
            df, self._base_factor_name(factor_name), factor_category,
            is_market=False,
        )

    def _import_market_factor(
        self,
        df: pd.DataFrame,
        factor_name: str,
        factor_category: str = '',
    ) -> int:
        """导入市场级因子（兼容旧接口）"""
        return self._import_frame(
            df, self._base_factor_name(factor_name), factor_category,
            is_market=True,
        )

    def _extract_factor_name(self, filename: str) -> str:
        """从文件名提取因子名称（保留旧接口）"""
        return Path(filename).stem

    def _extract_category(self, file_path: Path, root_dir: Path) -> str:
        """从文件路径中提取因子分类

        Args:
            file_path: 文件路径
            root_dir: 数据根目录

        Returns:
            分类路径（如 'Fama-French因子/三因子'）
        """
        try:
            rel = file_path.parent.relative_to(root_dir)
            return str(rel).replace('\\', '/')
        except ValueError:
            return ''
