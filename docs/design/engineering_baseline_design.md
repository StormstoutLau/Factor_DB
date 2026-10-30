# 工程化基线设计（CI + 静态检查 + CLI 冒烟）

> 日期：2026-10-30
> 依据：[调研](../research/engineering_baseline_research.md)

## 一、CI 工作流

文件：`.github/workflows/ci.yml`

| 项 | 值 |
|----|----|
| 触发 | `push`、`pull_request`（全部分支） |
| Runner | `ubuntu-latest` |
| Python | 3.12（与本地 `py -3` 一致） |
| 安装 | `pip install -e ".[dev]"`（`dev` = pytest + ruff） |
| 步骤 | ① `ruff check .` ② `python -m pytest -q` |
| 不执行 | 生产库验收（`scripts/validate_production.py`）、benchmarks |

## 二、静态检查配置

`pyproject.toml`：

```toml
[tool.ruff]
target-version = "py39"
exclude = [".git", ".trae", ".screenshots", "data", "factor_db.egg-info", ".pytest_cache"]

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F"]
```

- `E4` 导入相关、`E7` 语句相关、`E9` 语法/运行时错误、`F` pyflakes（未使用导入/变量、空 f-string）。
- 不启用 `I`（导入排序）与 `B`（bugbear），避免一次引入大量非语义改动。

## 三、CLI 接口变更

| 函数 | Before | After |
|------|--------|-------|
| `cli.main` | `main()`，内部 `parser.parse_args()` 读 `sys.argv` | `main(argv=None)`，`parser.parse_args(argv)`；入口点 `Factor_DB.cli:main` 不变 |
| `cmd_query` | `c.fetchdf(args.sql)`（DuckDB 1.x 报 TypeError） | `c.execute(args.sql).fetchdf()` |
| `cmd_list_factors` | `c.fetchdf("SELECT ...")`（同上） | `c.execute("SELECT ...").fetchdf()` |

## 四、测试设计（`tests/test_cli.py`）

| 用例 | 断言 |
|------|------|
| `test_no_command_prints_help` | `main([])` 输出含 `usage` / `factor-db` |
| `test_stats_lists_table_counts` | 临时库 1 行 `daily_prices` → 输出含 `daily_prices: 1 rows` |
| `test_query_prints_dataframe` | `SELECT 1 AS x` → 输出含列名（同时覆盖 `fetchdf` 修复） |
| `test_list_factors_reads_view` | 手工建 `factor_data` 视图 → 输出含 `pe`（覆盖第二个修复） |
| `test_cache_clear_prints_message` | 输出含「缓存」 |

隔离方式：`tempfile.mkdtemp()`；每个用例结束 `DuckDBConnection._instances.clear()`；输出用 `redirect_stdout` 捕获。

## 五、判定标准

1. `ruff check .` 零告警。
2. 全量 `pytest` 通过（基线 310 + 新增 5 = 315）。
3. 本地与 CI 使用同一命令，避免「本地绿、CI 红」。
