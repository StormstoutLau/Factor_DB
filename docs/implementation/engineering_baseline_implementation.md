# 工程化基线实施

> 日期：2026-10-30
> 依据：[设计](../design/engineering_baseline_design.md)

## 一、文件清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `.github/workflows/ci.yml` | 新增 | push/PR → ruff + pytest |
| `pyproject.toml` | 修改 | 新增 `[project.optional-dependencies].dev`、`[tool.ruff]`、`[tool.ruff.lint]` |
| `tests/test_cli.py` | 新增 | 5 个 CLI 冒烟用例 |
| `cli.py` | 修改 | `main(argv=None)`；修复 `cmd_query` / `cmd_list_factors` 的 `fetchdf` 调用；删除未使用变量 `p_cache` |
| 40 个源/测试文件 | 修改（机械） | `ruff --fix` 清理 67 项未使用导入 / 空 f-string |
| `tests/test_migration.py` 等 4 文件 | 修改（人工） | 6 个 F841 用 `# noqa: F841` 保留（变量被 DuckDB `FROM df` 字符串引用） |

## 二、执行步骤

1. `py -3 -m ruff check . --select E4,E7,E9,F --fix` → 67 fixed。
2. 人工处理剩余 6 项：
   - `cli.py`：删除未使用的 `p_cache =` 赋值（保留 parser 注册）。
   - 4 个测试中 `df = pd.DataFrame(rows)` 加 `# noqa: F841 - 供 DuckDB 的 FROM df 引用`。
   - `tests/test_migration.py` 的 `stats` 改为断言 `self.assertIn('factors', stats)`（同时补强测试）。
3. 新增 `tests/test_cli.py`，先跑 → 发现 `cmd_query` TypeErrors → 修复后转绿。
4. `py -3 -m ruff check .` → All checks passed；`pytest tests/test_cli.py` → 5 passed。

## 三、验证

| 命令 | 结果 |
|------|------|
| `py -3 -m ruff check .` | All checks passed |
| `py -3 -m pytest tests/test_cli.py -q` | 5 passed (0.71 s) |
| `py -3 -m pytest -q` | 315 passed（见本轮末尾） |

## 四、风险与回滚

| 风险 | 缓解 |
|------|------|
| 机械改动面大（40 文件） | 全部为删除未使用导入/空 f-string，无逻辑变更；由 315 个测试兜底 |
| CI 首次运行可能因依赖安装失败 | 失败时按日志拆 `dev` extras，不影响本地 |
| 回滚 | 删除 workflow 与 ruff 配置即可；代码改动可 `git revert` |
