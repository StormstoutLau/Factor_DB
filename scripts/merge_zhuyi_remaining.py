r"""
Merge remaining zhuyi data to <ASHARE_DATA_PATH>:
1. fundamental_data/ (3 source pkl) → raw_tushare/
2. financial_construction_factors/ (4 CSV → pkl) → construction_factors/
3. price_volume_factors/ (2 CSV → pkl) → price_volume_factors/

路径可用环境变量覆盖：
    ZHUYI_DATA_PATH（默认 F:\Code_Hub\data_archive\zhuyi）
    ASHARE_DATA_PATH（默认 E:\Ashare_data）
"""
import os
import shutil
import pandas as pd

ZH = os.getenv('ZHUYI_DATA_PATH', r"F:\Code_Hub\data_archive\zhuyi")
ASH = os.getenv('ASHARE_DATA_PATH', r"E:\Ashare_data")

results = []

# === Task 1: fundamental_data → raw_tushare ===
print("=== Task 1: fundamental_data → raw_tushare ===")
src = os.path.join(ZH, "fundamental_data")
dst = os.path.join(ASH, "raw_tushare")
os.makedirs(dst, exist_ok=True)
for f in os.listdir(src):
    if f.endswith('.pkl'):
        s, d = os.path.join(src, f), os.path.join(dst, f)
        shutil.copy2(s, d)
        sz = os.path.getsize(d) / 1024 / 1024
        print(f"  {f} → raw_tushare/ ({sz:.1f} MB)")
        results.append(f"raw_tushare/{f}")

# === Task 2: financial_construction_factors CSV → pkl → construction_factors ===
print("\n=== Task 2: financial_construction_factors → construction_factors ===")
src = os.path.join(ZH, "financial_construction_factors")
dst = os.path.join(ASH, "construction_factors")
os.makedirs(dst, exist_ok=True)
for f in os.listdir(src):
    if f.endswith('.csv'):
        csv_path = os.path.join(src, f)
        pkl_name = f.replace('.csv', '.pkl')
        pkl_path = os.path.join(dst, pkl_name)

        df = pd.read_csv(csv_path, index_col=0)
        df.index = pd.to_datetime(df.index)
        df.to_pickle(pkl_path)
        sz = os.path.getsize(pkl_path) / 1024 / 1024
        print(f"  {f} → construction_factors/{pkl_name} ({df.shape}, {sz:.1f} MB)")
        results.append(f"construction_factors/{pkl_name}")

# === Task 3: price_volume_factors CSV → pkl → price_volume_factors ===
print("\n=== Task 3: price_volume_factors → price_volume_factors ===")
src = os.path.join(ZH, "price_volume_factors")
dst = os.path.join(ASH, "price_volume_factors")
os.makedirs(dst, exist_ok=True)
for f in os.listdir(src):
    if f.endswith('.csv'):
        csv_path = os.path.join(src, f)
        pkl_name = f.replace('.csv', '.pkl')
        pkl_path = os.path.join(dst, pkl_name)

        df = pd.read_csv(csv_path, index_col=0)
        df.index = pd.to_datetime(df.index)
        df.to_pickle(pkl_path)
        sz = os.path.getsize(pkl_path) / 1024 / 1024
        print(f"  {f} → price_volume_factors/{pkl_name} ({df.shape}, {sz:.1f} MB)")
        results.append(f"price_volume_factors/{pkl_name}")

print(f"\n=== Done: {len(results)} files merged ===")
for r in results:
    print(f"  {r}")