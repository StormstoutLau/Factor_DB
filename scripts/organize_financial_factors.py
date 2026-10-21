"""
重组 financial_factors 文件夹：按分类移动 .pkl 文件
"""
import os
import shutil

BASE = os.getenv('ASHARE_DATA_PATH', r"E:\Ashare_data")

# Step 1: 重命名根目录
old_dir = os.path.join(BASE, "财务指标因子")
new_dir = os.path.join(BASE, "financial_factors")
if os.path.exists(old_dir) and not os.path.exists(new_dir):
    os.rename(old_dir, new_dir)
    print(f"Renamed: 财务指标因子 -> financial_factors")
elif os.path.exists(new_dir):
    print("financial_factors already exists")
else:
    print("Source not found, skipping rename")

# Step 2: 分类定义
CLASSIFICATION = {
    "profitability": [
        "roe", "roe_dt", "roe_waa", "roe_yearly", "roe_yoy",
        "roa", "roa_yearly", "roa2_yearly", "roa_dp",
        "roic", "gross_margin", "grossprofit_margin",
        "netprofit_margin", "ebit", "ebitda", "npta", "op_income",
    ],
    "growth": [
        "or_yoy", "op_yoy", "netprofit_yoy", "assets_yoy", "eqt_yoy",
        "equity_yoy", "tr_yoy", "ebt_yoy", "dt_netprofit_yoy", "dt_eps_yoy",
        "basic_eps_yoy", "op_of_gr", "ebit_of_gr", "profit_to_gr", "gc_of_gr",
    ],
    "valuation": [
        "bps", "bps_yoy", "cfps", "cfps_yoy", "ocfps",
        "ebit_ps", "fcff_ps", "fcfe_ps", "diluted2_eps",
        "retainedps", "surplus_rese_ps", "undist_profit_ps",
        "capital_rese_ps", "total_mv", "retained_earnings", "extra_item",
    ],
    "leverage_solvency": [
        "debt_to_assets", "debt_to_eqt", "dp_assets_to_eqt", "assets_to_eqt",
        "current_ratio", "quick_ratio", "cash_ratio",
        "eqt_to_debt", "eqt_to_interestdebt", "eqt_to_talcapital",
        "currentdebt_to_debt", "longdeb_to_debt", "int_to_talcap",
        "tangibleasset_to_debt", "tangasset_to_intdebt", "interestdebt",
        "netdebt", "invest_capital",
    ],
    "cash_flow": [
        "fcff", "fcfe", "ocf_to_debt", "ocf_to_shortdebt", "ocf_yoy",
        "profit_dedt", "profit_to_op", "current_exint", "noncurrent_exint",
    ],
    "operating_efficiency": [
        "ar_turn", "assets_turn", "fa_turn", "ca_turn", "turn_days",
        "working_capital", "networking_capital", "fixed_assets",
        "tangible_asset", "ca_to_assets", "nca_to_assets",
        "tbassets_to_totalassets",
    ],
    "quarterly": [
        "q_roe", "q_dt_roe", "q_op_qoq", "q_sales_yoy", "q_npta",
        "q_gc_to_gr", "q_saleexp_to_gr", "q_ocf_to_sales",
    ],
    "cost_structure": [
        "cogs_of_sales", "expense_of_sales", "adminexp_of_gr",
        "finaexp_of_gr", "saleexp_to_gr", "impai_ttm",
    ],
}

# 验证全部 101 个因子无遗漏
all_classified = set()
for items in CLASSIFICATION.values():
    all_classified.update(items)
assert len(all_classified) == 101, f"Expected 101, got {len(all_classified)}"

# Step 3: 创建子目录并移动文件
moved = 0
not_found = []

for category, factors in CLASSIFICATION.items():
    cat_dir = os.path.join(new_dir, category)
    os.makedirs(cat_dir, exist_ok=True)

    for factor in factors:
        src = os.path.join(new_dir, f"{factor}.pkl")
        dst = os.path.join(cat_dir, f"{factor}.pkl")
        if os.path.exists(src):
            shutil.move(src, dst)
            moved += 1
        else:
            not_found.append(factor)

# Step 4: 统计
print(f"\nMoved: {moved} files")
if not_found:
    print(f"Not found ({len(not_found)}): {not_found}")

for cat in CLASSIFICATION:
    cat_dir = os.path.join(new_dir, cat)
    count = len(os.listdir(cat_dir))
    print(f"  {cat:25s}: {count:3d} files")

print(f"\nTotal: {moved} files organized into {len(CLASSIFICATION)} categories")