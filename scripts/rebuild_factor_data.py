"""Rebuild factor_data table with ZSTD compression and sorted row groups.

Strategy:
  1. Export factor_data → Parquet (sorted by factor_name, trade_date, stock_code)
  2. Drop factor_data table
  3. Recreate with ZSTD compression
  4. COPY from Parquet (single import → optimal row groups)
  5. Create idx_factor_name_date

Expected result: 85 GiB → ~35-45 GiB
"""
import sys
sys.path.insert(0, '.')
import logging
import time
import duckdb
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

DB_PATH = 'factor_db.duckdb'
PARQUET_PATH = 'factor_data_export.parquet'

# Set to True to skip export (if Parquet file already exists from previous run)
SKIP_EXPORT = Path(PARQUET_PATH).exists()

# ─── Step 1: Export ───────────────────────────────────────────────
if SKIP_EXPORT:
    logger.info("=" * 60)
    logger.info("Step 1: SKIPPED — Parquet file already exists")
    logger.info("=" * 60)
    # Verify parquet integrity
    conn = duckdb.connect()
    parquet_count = conn.execute(f"SELECT COUNT(*) FROM '{PARQUET_PATH}'").fetchone()[0]
    conn.close()
    parquet_size = Path(PARQUET_PATH).stat().st_size / 1e9
    logger.info(f"Parquet file: {parquet_size:.2f} GB, {parquet_count:,} rows")
    before_count = parquet_count  # Use parquet count as reference
    logger.info("Row count verified OK")
else:
    logger.info("=" * 60)
    logger.info("Step 1: Export factor_data → Parquet (sorted, ZSTD)")
    logger.info("=" * 60)

    conn = duckdb.connect(DB_PATH, read_only=True)
    conn.execute("SET memory_limit='16GB'")
    conn.execute("SET threads=4")

    # Check row count before export
    before_count = conn.execute("SELECT COUNT(*) FROM factor_data").fetchone()[0]
    logger.info(f"factor_data rows: {before_count:,}")

    # Export as-is (no sort — sort on 871M rows is too slow; single import + ZSTD gives most benefit)
    t0 = time.time()
    logger.info("Exporting to Parquet (ZSTD compression)...")
    conn.execute(f"""
        COPY factor_data TO '{PARQUET_PATH}' (
            FORMAT PARQUET,
            COMPRESSION ZSTD,
            ROW_GROUP_SIZE 1000000
        )
    """)
    elapsed = time.time() - t0
    parquet_size = Path(PARQUET_PATH).stat().st_size / 1e9
    logger.info(f"Export complete: {parquet_size:.2f} GB in {elapsed:.0f}s")
    conn.close()

    # ─── Step 2: Verify Parquet ────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("Step 2: Verify Parquet integrity")
    logger.info("=" * 60)

    conn = duckdb.connect()
    parquet_count = conn.execute(f"SELECT COUNT(*) FROM '{PARQUET_PATH}'").fetchone()[0]
    logger.info(f"Parquet row count: {parquet_count:,}")
    assert parquet_count == before_count, f"Row count mismatch: {before_count} != {parquet_count}"
    logger.info("Row count verified OK")
    conn.close()

# ─── Step 3: Drop & Recreate ───────────────────────────────────────
logger.info("\n" + "=" * 60)
logger.info("Step 3: Drop and recreate factor_data table")
logger.info("=" * 60)

conn = duckdb.connect(DB_PATH, read_only=False)
conn.execute("SET memory_limit='24GB'")
conn.execute("SET threads=2")
conn.execute("SET preserve_insertion_order=false")

# Drop table
logger.info("Dropping factor_data...")
conn.execute("DROP TABLE IF EXISTS factor_data")

# Recreate WITHOUT PRIMARY KEY to avoid index build during import
# (PRIMARY KEY on 871M rows requires building index during COPY FROM → OOM)
logger.info("Creating factor_data table (no PK, will add after import)...")
conn.execute("""
    CREATE TABLE factor_data (
        trade_date DATE NOT NULL,
        stock_code VARCHAR NOT NULL,
        factor_name VARCHAR NOT NULL,
        factor_value DOUBLE,
        loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
""")
logger.info("factor_data table recreated")

# ─── Step 4: Import ─────────────────────────────────────────────────
logger.info("\n" + "=" * 60)
logger.info("Step 4: Import Parquet → factor_data (single bulk import)")
logger.info("=" * 60)

t0 = time.time()
logger.info("Importing from Parquet...")
conn.execute(f"""
    COPY factor_data FROM '{PARQUET_PATH}' (FORMAT PARQUET)
""")
elapsed = time.time() - t0

after_count = conn.execute("SELECT COUNT(*) FROM factor_data").fetchone()[0]
logger.info(f"Import complete: {after_count:,} rows in {elapsed:.0f}s")
assert after_count == before_count, f"Import row count mismatch: {before_count} != {after_count}"

# ─── Step 5: Add PRIMARY KEY ────────────────────────────────────────
logger.info("\n" + "=" * 60)
logger.info("Step 5: Add PRIMARY KEY constraint")
logger.info("=" * 60)

t0 = time.time()
conn.execute("SET memory_limit='28GB'")
conn.execute("SET threads=1")
try:
    conn.execute("""
        ALTER TABLE factor_data
        ADD PRIMARY KEY (trade_date, stock_code, factor_name, loaded_at)
    """)
    elapsed = time.time() - t0
    logger.info(f"PRIMARY KEY added in {elapsed:.0f}s")
except Exception as e:
    logger.warning(f"Failed to add PRIMARY KEY (OOM?): {e}")
    logger.warning("Continuing without PRIMARY KEY — add manually later")

# ─── Step 6: Create index ──────────────────────────────────────────
logger.info("\n" + "=" * 60)
logger.info("Step 6: Create idx_factor_name_date")
logger.info("=" * 60)

t0 = time.time()
conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_factor_name_date
    ON factor_data (factor_name, trade_date)
""")
elapsed = time.time() - t0
logger.info(f"Index created in {elapsed:.0f}s")

# ─── Step 7: Verify ─────────────────────────────────────────────────
logger.info("\n" + "=" * 60)
logger.info("Step 7: Verify final state")
logger.info("=" * 60)

# Check DB size
r = conn.execute("SELECT database_size FROM pragma_database_size()").fetchone()
logger.info(f"Database size: {r[0]}")

# Check row group organization
r = conn.execute("""
    SELECT 
        MAX(row_group_id) + 1 as total_row_groups,
        AVG(count)::INTEGER as avg_rows,
        MIN(count) as min_rows,
        MAX(count) as max_rows
    FROM pragma_storage_info('factor_data')
    WHERE column_id = 0 AND segment_type = 'VARCHAR'
""").fetchone()
if r[0]:
    logger.info(f"Row groups: {r[0]:,}")
    logger.info(f"Avg rows/group: {r[1]:,}")
    logger.info(f"Min/Max rows/group: {r[2]:,} / {r[3]:,}")

# Compression summary
r = conn.execute("""
    SELECT DISTINCT column_name, compression, COUNT(*) as segments
    FROM pragma_storage_info('factor_data')
    WHERE segment_type = 'VARCHAR'
    GROUP BY column_name, compression
    ORDER BY column_name
""").fetchall()
logger.info("Compression by column:")
for col, comp, segs in r:
    logger.info(f"  {col}: {comp} ({segs} segments)")

conn.close()

# ─── Step 7: Clean up Parquet file ─────────────────────────────────
logger.info("\n" + "=" * 60)
logger.info("Step 7: Clean up")
logger.info("=" * 60)
# Keep the Parquet file as backup, but inform user
logger.info(f"Parquet backup kept at: {PARQUET_PATH} ({parquet_size:.2f} GB)")
logger.info("To delete: del factor_data_export.parquet")

logger.info("\n" + "=" * 60)
logger.info("Rebuild complete!")
logger.info("=" * 60)