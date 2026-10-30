"""
Convert all CSV files in raw_fundamentals to pkl format.
CSV format: date index + stock columns
Delete original CSV after conversion.
"""
import os
import pandas as pd

BASE = os.path.join(os.getenv('ASHARE_DATA_PATH', r"E:\Ashare_data"), "raw_fundamentals")

count_total = 0
count_converted = 0
count_deleted = 0

# Walk through all subdirectories
for root, dirs, files in os.walk(BASE):
    for f in files:
        if f.endswith('.csv'):
            count_total += 1
            csv_path = os.path.join(root, f)
            pkl_path = os.path.join(root, f.replace('.csv', '.pkl'))

            # Read CSV
            df = pd.read_csv(csv_path, index_col=0)
            df.index = pd.to_datetime(df.index)
            # Convert to pkl
            df.to_pickle(pkl_path)
            count_converted += 1

            # Delete CSV
            os.remove(csv_path)
            count_deleted += 1

            print(f"Converted: {csv_path} → {pkl_path} ({df.shape})")

print(f"\nDone: total={count_total}, converted={count_converted}, deleted={count_deleted}")
