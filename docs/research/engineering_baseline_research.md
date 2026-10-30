# 工程化基线调研（CI + 静态检查 + CLI 冒烟）

> 日期：2026-10-30
> 目标：在不引入生产库依赖的前提下，为 310 个测试建立自动门禁

## 一、现状（实测）

| 项 | 事实 | 证据 |
|----|------|------|
| CI | 仓库无 `.github/`，无任何 workflow | 根目录列举 |
| 静态检查 | `pyproject.toml` 无 ruff/mypy/black 配置 | 文件内容 |
| 测试规模 | 310 passed（110 s，本地 `py -3`） | 全量 pytest |
| CLI 测试 | 无 `tests/test_cli.py`，`main()` 直接读 `sys.argv` | `cli.py` |
| 代码问题 | ruff（E4/E7/E9/F）报 73 项：F401 ×61、F541 ×6、F841 ×6 | `ruff check --statistics` |

### 1.1 新增 CLI 测试后立即暴露的真实缺陷

`cmd_query` 与 `cmd_list_factors` 都调用 `c.fetchdf(sql)`。DuckDB 1.5 的 `fetchdf()` 已不接受 SQL 参数
（`TypeError: fetchdf(): incompatible function arguments`），即 **`factor-db query` / `factor-db list-factors` 在生产版本下不可用**。
说明 CLI 属于「无测试覆盖 → 已腐化」的典型区域。

## 二、目标与范围

目标：
1. 每次 push / PR 自动执行 `ruff check` + `pytest`（Linux，临时库）。
2. 建立最小静态检查基线（先选不易误报、可自动修复的规则集）。
3. 为 CLI 增加冒烟测试，修复已腐化的两个命令。

范围外：
- 不在 CI 跑 46 GiB 生产库验收（体积/成本不允许，仍由 `scripts/validate_production.py` 手动执行）。
- 不引入 mypy / coverage 阈值（当前类型标注不完整，贸然开启会产生大量噪音）。
- 不改动业务逻辑与数据模型。

## 三、方案取舍

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| CI 平台 | GitHub Actions / 本地 pre-commit | GitHub Actions | 仓库已托管 origin，零额外依赖 |
| 规则集 | 全量 ruff（含 I/UP/B/SIM）/ 保守集 | 保守集 `E4,E7,E9,F` | 现网 73 项可一次清完；扩展规则会引入大量风格改动，超出本任务 |
| 修复方式 | 全部手动 / `--fix` + 人工复核 | `--fix`（67 项）+ 人工 6 项 | 67 项均为安全的未使用导入/空 f-string |
| 依赖安装 | `requirements.txt` / `pip install -e .[dev]` | dev extras | 与 `pyproject` 单一事实源一致 |
| CLI 可测性 | patch `sys.argv` / `main(argv=None)` | `main(argv=None)` | 一行改动，消除测试对全局状态的依赖 |

## 四、风险

| 风险 | 缓解 |
|------|------|
| `--fix` 误删被字符串/SQL 引用的变量 | 6 个 F841 均为 DuckDB `FROM df` 引用，已用 `# noqa` 保留，未删除 |
| CI 安装 akshare 等重依赖耗时长 | 接受；后续如需提速可拆分 test extras |
| 规则过窄漏掉真实问题 | 先立基线，后续按需扩到 `I`/`B`，并有 CI 保证扩规则时可见全量差异 |

## 五、结论

工程化基线以「GitHub Actions + ruff 保守集 + CLI 冒烟测试」落地；顺带修复 `cmd_query` / `cmd_list_factors` 的 `fetchdf` 调用缺陷。
