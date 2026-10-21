# HIGH 级问题修复调研文档

> 日期：2026-10
> 范围：HIGH 1（SQL 拼接）、HIGH 2（README/宽表入口）、HIGH 3（硬编码路径与内存）
> 方法：官方文档 + 行业标准 + 学术资料 + 社区案例 + 仓库代码实证；结论区分「已核实 / 推断 / 证伪」

---

## 1. 结论摘要

| 问题 | 修复方向 | 依据 |
|------|----------|------|
| HIGH 1 | 值 → 命名参数绑定；标识符 → 白名单 + 双引号引用；缓存 key 纳入参数 | E1–E10 |
| HIGH 2 | 入口格式校验并抛 `ValueError`；README 改为宽表示例 | 仓库代码实证（C1–C3） |
| HIGH 3 | 内存/线程/临时目录/数据根目录收进 `LoaderConfig` + 环境变量 | E11、E12 |

---

## 2. 证据锚点

### 2.1 官方文档（权威）

| 编号 | 事实 | 出处 |
|------|------|------|
| E1 | DuckDB Python 支持命名参数 `$name`，参数以 `dict[str, Any]` 传入，同一参数可在 SQL 中复用 | [Python DB API — Named Parameters](https://duckdb.org/docs/stable/clients/python/dbapi)、[Prepared Statements — Named Parameters](https://duckdb.org/docs/stable/sql/query_syntax/prepared_statements) |
| E2 | 参数化查询（Prepared Statements）是防 SQL 注入的首选防御 | [OWASP SQL Injection Prevention Cheat Sheet — Defense Option 1](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html) |
| E3 | 表名、列名、排序方向等**不能**使用绑定变量，应使用允许列表校验 | 同上，Defense Option 3：*“parts of SQL queries that can't use bind variables, such as table names, column names, or sort order indicators”* |
| E4 | DuckDB 标识符用双引号包裹；内嵌双引号通过重复双引号转义（`"IDENTIFIER ""X"""`） | [Keywords and Identifiers](https://duckdb.org/docs/stable/sql/dialect/keywords_and_identifiers) |
| E5 | DuckDB 字符串字面量用单引号；内嵌单引号用 `''` 转义 | [Literal Types — String Literals](https://www.duckdb.org/docs/stable/sql/data_types/literal_types) |
| E6 | `memory_limit` 默认值为 80% 物理内存；`threads` 可配置；`temp_directory` 默认 `<database_file_name>.tmp` | [Configuration Overview](https://duckdb.org/docs/stable/configuration/overview)、[Tuning Workloads — Spilling to Disk](https://www.duckdb.org/docs/stable/guides/performance/how_to_tune_workloads) |
| E7 | Python 官方明确警告：不要用字符串操作拼接 SQL | [sqlite3 — How to use placeholders](https://docs.python.org/3/library/sqlite3.html#how-to-use-placeholders-to-bind-values-in-sql-queries) |

### 2.2 学术资料（同行评审）

| 编号 | 资料 | 与本任务的关系 |
|------|------|----------------|
| E8 | Halfond & Orso, *AMNESIA: Analysis and Monitoring for NEutralizing SQL-injection Attacks*, ASE 2005, DOI [10.1145/1101908.1101935](https://doi.org/10.1145/1101908.1101935)（Crossref 记录 331 次引用） | 将 SQL 注入定义为「用户输入被当作代码」的边界混淆；支撑在查询构造层做值与代码分离 |
| E9 | Halfond & Orso, *Preventing SQL injection attacks using AMNESIA*, ICSE 2006, DOI [10.1145/1134285.1134416](https://doi.org/10.1145/1134285.1134416) | 同上，工具化防御 |
| E10 | Fadillah & Servanda, *Analisis Efektivitas Teknik Parameterized Queries dalam Mencegah Serangan SQL Injection Menggunakan DVWA*, 2024, DOI [10.53990/jupiter.v5i2.323](https://doi.org/10.53990/jupiter.v5i2.323) | 15 个 payload 实验中参数化查询阻止全部测试注入；小样本，仅作旁证 |
| E11 | *A Survey on the Detection of SQL Injection Attacks and Their Countermeasures*, JIPS 2015, DOI [10.3745/jips.03.0024](https://doi.org/10.3745/jips.03.0024) | 注入检测与防御方法综述背景 |

### 2.3 社区案例

| 编号 | 案例 | 要点 |
|------|------|------|
| E12 | [Stack Overflow #77884985 — SQL-Injection in duckdb-queries on pandas dataframes](https://stackoverflow.com/questions/77884985) | 采纳答案确认 `duckdb.execute("... WHERE id=?", (user_input,)).df()` 对 DataFrame 查询同样生效 |
| E13 | McKinsey Lilli 事件（2026-02，CodeWall 披露；[媒体报道](https://treblle.com/blog/codewall-hack-mckinsey-ai-platform-lilli)） | *“search endpoint correctly parameterized its query values… but the JSON keys, the field names themselves, were stringed directly into the SQL query”*——值已参数化，但标识符直接拼接仍导致注入。该来源为二手媒体，仅作案例佐证 |

### 2.4 仓库代码实证

| 编号 | 事实 | 位置 |
|------|------|------|
| C1 | `load_factor_to_wide` 要求宽表（index=日期，columns=股票代码），README 示例传长表 | `loaders/daily_loader.py#L395-L437` vs `README.md#L100-L107` |
| C2 | `load_factor_to_wide` 失败被捕获后 `return 0`，调用方无法区分成功与失败 | `loaders/daily_loader.py#L485-L489` |
| C3 | `load_factor_to_wide` 仅被测试调用，无生产脚本依赖，失败改抛异常无破坏面 | `grep load_factor_to_wide`：仅 `tests/test_wide_loader.py`、`tests/test_wide_query.py` |
| C4 | `daily_loader` 硬编码 `20GB`/`4`/`F:\...tmp` 各两处 | `loaders/daily_loader.py#L49-L54`、`#L440-L441` |
| C5 | `import_phase1_ashare.py` 的 `FACTOR_DIR` 指向已改名目录，且用 `glob('*.pkl')` 无法覆盖 8 个子目录 | `scripts/import_phase1_ashare.py#L35`、`#L306`；`E:\Ashare_data\financial_factors\<category>\*.pkl` 实证 |
| C6 | `osint_query.get_data_by_source` 同一 `WHERE {where_clause}` 在 UNION ALL 出现两次，但位置参数只传一份 | `query/osint_query.py#L50-L71`（静态推演，待红灯测试确认） |
| C7 | 现有因子文件名均为 ASCII snake_case | `E:\Ashare_data` 下 `financial_factors`/`raw_fundamentals`/`construction_factors` 文件名实测 |

---

## 3. 幻觉排除记录

| 原说法 | 核实结果 | 处理 |
|--------|----------|------|
| 「DuckDB 不支持命名参数」 | 证伪（E1、E12） | 采用 `$name` 命名参数 |
| 「`temp_directory` 没有默认值」 | 证伪：默认 `<db_file>.tmp`（E6） | 默认值 `f"{db_path}.tmp"` |
| 「因子名含引号导致语法错误（CSMAR 文件名来源）」 | 未观察到实例（C7） | 保留为潜在风险与测试用例，不写成事实 |
| 「本地单机库存在 Web 注入攻击面」 | 高估：无外部输入入口 | 目标为健壮性与同类风险清零 |
| 「OSINT UNION ALL 缺参 bug 已确认」 | 仅静态推演（C6） | 标记待测试确认 |
| 「`memory_limit=20GB` 是 DuckDB 默认」 | 证伪：默认 80% RAM（E6） | 保留为显式保守值，改为可配置 |

---

## 4. 设计含义

1. **命名参数而非位置参数**：E1 允许同一参数在 SQL 中复用，解决 `get_ic_series` 的 `date_filter` 三次出现与 OSINT UNION ALL 两次 WHERE 的问题；位置参数需要按出现顺序重复传值，易错。
2. **值与标识符分开处理**：E2/E3 明确绑定变量只覆盖值；标识符必须白名单（E3）。E13 提供了「值已参数化但标识符拼接仍被注入」的真实案例。
3. **转义规则以官方为准**：标识符双引号 + 重复双引号（E4）；字符串字面量单引号 + `''`（E5）。仅在不能绑定的位置使用。
4. **配置外置**：E6 给出官方默认值，E12（12-Factor）支持环境变量；保留现有默认行为，避免改变生产导入性能。
5. **测试优先**：对 C6 先写红灯测试，确认是否真实缺参，再决定修复方式。
