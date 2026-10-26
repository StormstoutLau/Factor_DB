# 遗留问题调研：DB 死空间、冷缓存、备份表

> 主题：`factor_history` 重排后遗留的三项收尾工作
> 日期：2026-10-25
> 前置：[DEVLOG 重排条目](../DEVLOG.md) / [ARCHITECTURE §7](../ARCHITECTURE.md) / [REVIEW_CHECKLIST 第九节](../spec/REVIEW_CHECKLIST.md)

---

## 1. 问题定义

| 编号 | 问题 | 当前状态 |
|------|------|----------|
| P1 | DB 文件含死空间 | 93.43 GB，其中 free blocks 35.3 GiB（`DROP` 旧表后 `CHECKPOINT` 仅部分回收） |
| P2 | 冷缓存首次查询慢 | 跨文件单因子 2024Q1 冷缓存 21–25s，热缓存 0.45s |
| P3 | 46 GB 备份表未删 | `factor_data_backup_20261023_162924` 实际约 11.6 GiB（见 §2，文档旧数字偏大） |

## 2. 库内事实（本机实测，只读连接）

### 2.1 文件与块占用

`CALL pragma_database_size()`：

| 字段 | 值 |
|------|-----|
| database_size | 93.4 GiB |
| block_size | 262,144 B（256 KiB） |
| total_blocks | 382,833 |
| used_blocks | 238,106（58.1 GiB） |
| free_blocks | **144,727（35.3 GiB）** |
| memory_usage / memory_limit | 131.2 MiB / 25.0 GiB |

### 2.2 各表 block 占用（`pragma_storage_info` 去重 `block_id` × 256 KiB，估算）

| 表 | 唯一 block 数 | 估算占用 |
|----|--------------|----------|
| `factor_history` | 118,455 | 28.9 GiB |
| `factor_wide` | 41,768 | 10.2 GiB |
| `factor_data_backup_20261023_162924` | 47,551 | **11.6 GiB** |
| `daily_prices` | 1,274 | 0.3 GiB |

合计约 51 GiB，其余约 7 GiB 为元数据与空表。**结论：备份表不是 46 GB，而是约 11.6 GiB**；文档中的 46 GB 数字需修正。

### 2.3 依赖关系

`duckdb_views()` 中唯一的非系统视图是 `factor_data`（`UNPIVOT(factor_wide)`），**没有任何视图依赖备份表**。备份表行数 1,683,530,224，与迁移前 `factor_data` 一致。

## 3. 外部行为实证（本机探针）

### E1 — `COPY FROM DATABASE` 保序且吞吐远高于排序

在 D:（NVMe）建两个临时库，从生产库只读复制前 5,000 万行：

```
preserve_insertion_order = True
CTAS 50M rows: 20.5s  (2,441,929 rows/s，含 F: HDD 冷读)
COPY FROM DATABASE 50M rows: 4.9s  (10,111,513 rows/s)
p1/p2 first: ('BF_CRASHRISK_CRASH_Cmdeq', 1991-01-01, '000001')
p1/p2 last : ('BF_Portfolio_LR_Lretndeq', 2009-06-01, 'MKT_MT_79_...')
文件大小：261 MB / 262 MB（压缩率一致）
```

首末行完全一致 → **压缩重建不会破坏 2026-10-25 的 `(factor_name, trade_date, stock_code)` 物理顺序**。新库文件大小与源一致 → 压缩算法保持一致。

### E2 — `COPY FROM DATABASE` 复制视图与索引

小库探针（表 + 索引 + 视图）：

```
tables: [('t',)]   views: [('v_t',)]   indexes: [('idx_t_k', False)]
view query: (10,)
```

与官方文档「copies the schema, including constraints, indexes, sequences, macros and the data」一致，视图亦被复制。

### E3 — 备份表可安全删除的判定条件

无视图依赖（§2.3）+ 行数一致 + 迁移时已抽样 6 个因子逐一对账（LT_debt_ratio / UTR_turnover / UTR_turnover_new / undist_profit_ps / undistr_porfit / working_capital）。删除前仍建议补一次 241 个宽表因子的行数聚合对账（见 §6 验收门）。

## 4. 官方与社区锚点

| 编号 | 结论 | 来源 |
|------|------|------|
| A1 | `VACUUM` 不回收空间；回收只能靠 `CHECKPOINT` 或整库复制 | https://www.duckdb.org/docs/lts/operations_manual/footprint_of_duckdb/reclaiming_space |
| A2 | `CHECKPOINT` 只合并「相邻行组删除比例约 ≥25%」的块，不会缩文件到最小 | https://duckdb.org/docs/current/sql/statements/checkpoint |
| A3 | 0.9.0 起仅在「被删行组位于文件尾部」时截断文件 | https://www.duckdb.org/2023/09/26/announcing-duckdb-090 |
| A4 | 压缩数据库的官方做法：`ATTACH` 新库 + `COPY FROM DATABASE db1 TO db2` | https://www.duckdb.org/docs/lts/operations_manual/footprint_of_duckdb/reclaiming_space |
| A5 | `COPY FROM DATABASE` 复制 schema（含约束/索引/序列/宏）与数据 | https://www.duckdb.org/docs/current/sql/statements/copy#copy-from-database--to |
| A6 | DuckDB 磁盘模式「designed to work best with SSD and NVMe disks；HDD 支持的但性能低，尤其写」 | https://duckdb.org/docs/current/guides/performance/environment |
| A7 | Buffer manager 缓存页直到库关闭或内存不足；受 `memory_limit` 约束 | https://duckdb.org/2024/07/09/memory-management |
| A8 | 社区扩展 `cache_prewarm`：把表块预热进 buffer pool / OS page cache，降低冷启动延迟（ClickBench，31 GB RAM 机器） | https://duckdb.org/community_extensions/extensions/cache_prewarm ；https://blog.peterxcli.dev/oss/duckdb/duckdb-cache-prewarm/ |
| A9 | 社区案例（matchlab 文档）：「DuckDB files do not shrink … file stays the size of its high-water mark. There is no `VACUUM FULL`, and `CHECKPOINT` will not do it either」 | https://uktrade.github.io/matchlab/guide/build-a-plan/ |
| A10 | Stack Overflow #66027598（9 票）：无 vacuum，用 `EXPORT`/`IMPORT`（旧版）或官方 reclaiming-space 页 | https://stackoverflow.com/questions/66027598/how-to-vacuum-reduce-file-size-on-duckdb |
| A11 | Stack Overflow #77188357：用 `PRAGMA database_size` / `PRAGMA storage_info('table')` 查空间与表级指标 | https://stackoverflow.com/questions/77188357/how-can-we-find-a-table-size-from-duckdb-file |
| A12 | `ATTACH` 支持多个 DuckDB 原生格式文件（跨库查询） | https://duckdb.org/docs/current/sql/statements/attach |
| A13 | Parquet 直查比 DuckDB 原生表慢 1.1–5.0×；反复查询应载入 DuckDB | https://duckdb.org/docs/current/guides/performance/file_formats |

## 5. 方案对比

### P1（死空间）+ P3（备份表）

| 方案 | 做法 | 结果 | 结论 |
|------|------|------|------|
| C1 | `VACUUM` | 不回收（A1） | 否决 |
| C2 | 仅 `CHECKPOINT` | 需相邻行组 ≥25% 删除（A2）；本次实测 69.69→93.43 GB 未缩 | 否决 |
| C3 | `EXPORT DATABASE` → `IMPORT DATABASE` | 得到干净库，但导出为 CSV/Parquet 中间态，额外 IO 与格式转换 | 可选（旧版做法） |
| C4 | **`COPY FROM DATABASE` 到新库文件** | 顺序/压缩/索引/视图保持（E1、E2）；一次性同时回收死空间与备份表 | **推荐** |

C4 空间与时间（**已实测，见 §9**）：
- 先删备份表：live 数据 ≈ 46.4 GiB
- 目标文件 **46.19 GiB**（`used_blocks=189,187`、`free_blocks=0`）；目标落 D: NVMe（剩余 199.9 GiB）
- 5.2B 行整库复制实测 **7,104 秒（118.4 分钟）**，吞吐仅 **3–5 MB/s**
- 估算被证伪：E1 的 10M rows/s 是小表探针（50M 行、内存热），大表复制受**有序写入单线程 + F: HDD 列读取**限制；探针期间 F: idle 80–94%、queue 0.1，说明瓶颈不在磁盘带宽而是单线程 sink
- 校验：行数、`loaded_at`、抽样因子、首末行、视图可用性

### P2（冷缓存）

| 方案 | 做法 | 依据 | 结论 |
|------|------|------|------|
| T1 | 压缩后把主库放到 D:（NVMe） | A6；冷读随机元数据由 NVMe 承担。**实测冷查询 21–25s → 0.92s**（见 §9） | **已执行（与 P1 同一次维护窗口完成）** |
| T2 | 主库留 F:，另建 `hot.duckdb` 于 D:，`ATTACH` 热因子子集 | A12；需要查询路由与热因子清单（当前无查询日志，难以确定子集） | 备选 |
| T3 | 启动时 `cache_prewarm` 预热 | A8；按表/大小预热，**不支持按谓词选热因子**；`factor_history` 28.9 GiB > 25 GiB 内存上限 | 备选（只适合小热表） |
| T4 | 冷因子转 Parquet 分区 + 视图 | A13；Parquet 直查慢 1.1–5×，且需重写 29 GiB | 暂不采用 |

### P3（备份表）

| 方案 | 做法 | 结论 |
|------|------|------|
| B1 | 立即 `DROP` 后 `CHECKPOINT` | 省 11.6 GiB，但死空间仍 35.3 GiB（回收有限） |
| B2 | **先补全量对账 → `DROP` → 同窗口执行 C4** | **推荐**：一次维护同时解决 P1/P3；旧 93.4 GB 文件可先保留为归档，校验通过后再删 |

## 6. 建议执行顺序（待确认后进入设计/实施）

1. **对账门（只读）**：241 个宽表因子在备份表与 `factor_history` 的行数逐因子一致；抽样 10 个因子非空计数一致
2. **删备份**：`DROP TABLE factor_data_backup_20261023_162924` + `CHECKPOINT`
3. **压缩重建**：`ATTACH 'D:\factor_db_compacted.duckdb' AS newdb; COPY FROM DATABASE factor_db TO newdb;`
4. **校验**：行数 5,203,062,078、`loaded_at` 非空数、抽样因子、首末行、`factor_data` 视图查询
5. **切换**：`DETACH` → 旧文件改名归档 → 新文件落到生产路径（若采用 T1，生产路径指向 D:）
6. **冷/热基准复测**：冷缓存单因子、热缓存单因子、matrix、stats
7. **清理**：确认无误后删除旧 93.4 GB 文件与 F: 临时目录

> 注意：`COPY FROM DATABASE` 需要独占写权限（DuckDB 单写者），执行期间查询服务必须停止。

## 7. 幻觉排除

| 候选说法 | 核查 | 结论 |
|----------|------|------|
| `VACUUM` 可回收 DuckDB 空间 | A1、A10 明确否定 | 证伪 |
| `CHECKPOINT` 必定缩小文件 | A2/A3：需相邻行组大量删除，且仅在文件尾部截断 | 证伪 |
| 备份表约 46 GB | §2.2 实测约 11.6 GiB | 证伪（文档数字偏差） |
| `COPY FROM DATABASE` 会打乱物理顺序 | E1 首末行一致 | 证伪 |
| `COPY FROM DATABASE` 不复制视图/索引 | E2 实测均已复制 | 证伪 |
| `cache_prewarm` 可按因子名预热热数据 | 官方扩展文档仅支持整表/大小限制，无谓词 | 证伪 |
| 压缩重建与 19.2 小时重排同量级 | E1 显示复制吞吐 10M rows/s，且无需排序 | 证伪（**实测 118.4 分钟**，比排序快约 10 倍） |
| 备份表被视图依赖、不能删 | §2.3：唯一视图依赖 `factor_wide` | 证伪 |
| 探针 10M rows/s 可外推到 5.2B 行 | §9 实测 3–5 MB/s，约 20 万行/s | 证伪（小表内存热、无 HDD 列读） |

## 8. 结论

- P1 与 P3 应合并成一次维护：**删备份 + `COPY FROM DATABASE` 压缩重建**，实测回收 47.24 GiB（93.43 → **46.19 GiB**），耗时 118.4 分钟
- P2 最优解不是软件层预热，而是**把压缩后的主库放到 NVMe**（D:），一次性消除 HDD 随机读瓶颈；热因子子库与 `cache_prewarm` 作为备选
- 所有结论均以本机探针 + 官方文档 + 社区案例（SO / matchlab / DuckDB 博客）交叉验证，未采纳无出处的推断

## 9. 执行结果（2026-10-26 实测）

**执行顺序**：对账门 → 删备份 → `COPY FROM DATABASE` 到 D: → 校验 → 符号链接切换 → 基准复测

| 阶段 | 结果 |
|------|------|
| 对账门 | 241 个宽表因子，备份表 vs `factor_history` 均 1,683,530,224 行，**0 差异** |
| 删备份 + CHECKPOINT | used_blocks 238,106 → 190,667（释放 11.6 GiB 逻辑空间），文件不缩小 |
| `COPY FROM DATABASE` | **7,104 秒（118.4 分钟）**；新库 46.19 GiB、total/used_blocks=189,187、free_blocks=0 |
| 校验 | 5,203,062,078 行；`loaded_at` 全非空；`factor_wide` 9,307,121 行 / 241 因子；5 抽样因子行数与日期区间一致；首末行顺序保持；`factor_data` 视图返回 5,624 列 |
| 切换 | 管理员创建符号链接 `F:\Coding\Factor_DB\factor_db.duckdb` → `D:\Factor_DB\factor_db_compacted.duckdb`；旧库改名 `factor_db_pre_compact_20261026.duckdb` |

**NVMe 基准（经生产符号链接）**：

| 场景 | 原 HDD（93.43 GB） | 新 NVMe（46.19 GB） |
|------|-------------------|---------------------|
| LOSEORWIN 2024Q1 冷 | 21–25s | **0.92s** |
| LOSEORWIN 2024Q1 热 | 0.45s | 0.23s |
| 单因子全量（211,515 行） | 0.63s | 0.39s |
| VPIN 2024Q1 | 7.5s 冷 / 1.40s 热 | 0.04s |
| `get_cross_section` | 0.05s | 0.02s |
| `get_factor_stats` | 0.05s | 0.02s |

**新发现（未预料）**：
- 通过符号链接打开时，DuckDB 把 `temp_directory` 自动解析到真实路径 `D:\Factor_DB\factor_db_compacted.duckdb.tmp`（溢写落在 NVMe，无需设 `FACTOR_DB_TEMP_DIR`）
- catalog 名由真实文件名派生为 `factor_db_compacted`；代码全部使用非限定表名，无影响，但外部脚本若写 `factor_db.` 限定需注意
- 旧库归档 93.43 GB 占 F: 空间，确认无需回滚后可删除
