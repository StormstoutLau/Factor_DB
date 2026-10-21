# ADR-0007: zhuyi 与 Ashare_data 数据源合并策略

- **状态**: 已接受
- **日期**: 2026-07

## 背景
存在两套独立的 A 股数据源：
- `F:\Code_Hub\data_archive\zhuyi`：120 个原始财务报表科目（Tushare 三大报表字段）
- `E:\Ashare_data`：101 个衍生财务比率/因子 + 行情数据

需决定如何合并以最大化因子库覆盖，同时避免重复导入。

## 决策
以 **Ashare_data 为主，zhuyi 为补充**，合并到统一目录结构：
```
E:\Ashare_data\
├── market_data/          17 行情文件（OHLCV + 元数据）
├── financial_factors/    101 衍生财务比率（8 类英文子目录）
├── raw_fundamentals/     120 原始财务报表科目（3 报表 × 10 子类）
├── raw_tushare/          3 个 Tushare 原始 API 响应（源数据）
├── construction_factors/ 4 个自建财务因子
└── price_volume_factors/ 2 个月度换手率因子
```

## 理由
- Ashare 质量更高（NaN 率 ~10% vs zhuyi ~15-53%）、覆盖更全（6507 vs 6231 只）、更新 15 天
- 两套数据**互补而非竞争**：zhuyi 提供原始科目（可自建因子），Ashare 提供已计算比率（可直接回测）
- 仅 2 个因子重叠（`ebit`、`current_ratio`），导入时去重

## 备选方案
- 全量导入 zhuyi：NaN 率高，质量差
- 只保留 Ashare：丢失 120 个原始科目，因子自建能力受限

## 后果
- ✅ 因子总数从 117 → 241（+124 个，去重后）
- ✅ 数据行数从 8.7 亿 → 16.8 亿
- ✅ 导入脚本 `import_phase4_factors.py` 实现去重逻辑
- ⚠️ 重复因子（`ebit`、`current_ratio`）以 Ashare 版本为准（质量更高）
