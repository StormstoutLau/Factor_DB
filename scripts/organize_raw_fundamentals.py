r"""
Copy zhuyi raw fundamental CSVs to <ASHARE_DATA_PATH>\raw_fundamentals\
and organize by financial statement + sub-category.

路径可用环境变量覆盖：
    ZHUYI_DATA_PATH（默认 F:\Code_Hub\data_archive\zhuyi）
    ASHARE_DATA_PATH（默认 E:\Ashare_data）
"""
import os
import shutil
import glob as _glob

ZHUYI_ROOT = os.getenv('ZHUYI_DATA_PATH', r"F:\Code_Hub\data_archive\zhuyi")
ASHARE_ROOT = os.getenv('ASHARE_DATA_PATH', r"E:\Ashare_data")

SRC = os.path.join(ZHUYI_ROOT, "fundamental_factors")
DST = os.path.join(ASHARE_ROOT, "raw_fundamentals")

CLASSIFICATION = {
    # ===== Balance Sheet (48) =====
    "balance_sheet/assets": [
        "money_cap", "trad_asset", "notes_receiv", "accounts_receiv",
        "oth_receiv", "prepayment", "inventories", "oth_cur_assets",
        "total_cur_assets", "accounts_receiv_bill",
        "lt_eqt_invest", "fix_assets", "cip", "intan_assets",
        "lt_amor_exp", "defer_tax_assets", "total_nca", "oth_nca",
        "fix_assets_total", "cip_total", "total_assets",
    ],
    "balance_sheet/liabilities": [
        "st_borr", "notes_payable", "acct_payable", "payroll_payable",
        "taxes_payable", "oth_payable", "oth_cur_liab",
        "non_cur_liab_due_1y", "total_cur_liab", "accounts_pay",
        "lt_borr", "defer_tax_liab", "defer_inc_non_cur_liab",
        "total_ncl", "total_liab", "contract_liab", "oth_pay_total",
    ],
    "balance_sheet/equity": [
        "total_share", "cap_rese", "undistr_porfit", "surplus_rese",
        "total_hldr_eqy_exc_min_int", "total_hldr_eqy_inc_min_int",
        "minority_int", "total_liab_hldr_eqy", "oth_comp_income",
        "oth_rcv_total",
    ],

    # ===== Income Statement (30) =====
    "income_statement/revenue": [
        "total_revenue", "revenue", "invest_income",
        "fv_value_chg_gain", "non_oper_income", "ass_invest_income",
    ],
    "income_statement/expenses": [
        "total_cogs", "oper_cost", "biz_tax_surchg", "sell_exp",
        "admin_exp", "fin_exp", "assets_impair_loss", "non_oper_exp",
        "rd_exp", "fin_exp_int_exp", "fin_exp_int_inc",
    ],
    "income_statement/profit": [
        "operate_profit", "total_profit", "income_tax",
        "n_income", "n_income_attr_p", "minority_gain",
        "oth_compr_income", "t_compr_income", "compr_inc_attr_p",
        "compr_inc_attr_m_s", "ebit", "basic_eps", "diluted_eps",
    ],

    # ===== Cash Flow Statement (42) =====
    "cash_flow/operating": [
        "c_fr_sale_sg", "recp_tax_rends", "c_fr_oth_operate_a",
        "c_inf_fr_operate_a", "c_paid_goods_s", "c_paid_to_for_empl",
        "c_paid_for_taxes", "oth_cash_pay_oper_act",
        "st_cash_out_act", "n_cashflow_act",
    ],
    "cash_flow/investing": [
        "c_disp_withdrwl_invest", "c_recp_return_invest",
        "n_recp_disp_fiolta", "stot_inflows_inv_act",
        "c_pay_acq_const_fiolta", "c_paid_invest",
        "stot_out_inv_act", "n_cashflow_inv_act",
    ],
    "cash_flow/financing": [
        "c_recp_borrow", "stot_cash_in_fnc_act", "free_cashflow",
        "c_prepay_amt_borr", "c_pay_dist_dpcp_int_exp",
        "oth_cashpay_ral_fnc_act", "stot_cashout_fnc_act",
        "n_cash_flows_fnc_act",
    ],
    "cash_flow/supplemental": [
        "net_profit", "finan_exp", "depr_fa_coga_dpba",
        "amort_intang_assets", "decr_inventories",
        "decr_oper_payable", "incr_oper_payable",
        "decr_def_inc_tax_assets", "im_net_cashflow_oper_act",
        "eff_fx_flu_cash", "n_incr_cash_cash_equ",
        "c_cash_equ_beg_period", "c_cash_equ_end_period",
        "im_n_incr_cash_equ", "beg_bal_cash", "end_bal_cash",
    ],
}

# Validate
all_classified = set()
for items in CLASSIFICATION.values():
    all_classified.update(items)
assert len(all_classified) == 120, f"Expected 120, got {len(all_classified)}"

# Step 1: Copy all CSVs to DST root
os.makedirs(DST, exist_ok=True)
csv_files = _glob.glob(os.path.join(SRC, "*.csv"))
copied = 0
for f in csv_files:
    dst_file = os.path.join(DST, os.path.basename(f))
    if not os.path.exists(dst_file):
        shutil.copy2(f, dst_file)
        copied += 1

print(f"Copied {copied} new CSV files to {DST}")
print(f"Total CSV files in DST: {len(_glob.glob(os.path.join(DST, '*.csv')))}")

# Step 2: Create subdirectories and move files
moved = 0
not_found = []
for category, factors in CLASSIFICATION.items():
    cat_dir = os.path.join(DST, category)
    os.makedirs(cat_dir, exist_ok=True)

    for factor in factors:
        src = os.path.join(DST, f"{factor}.csv")
        dst = os.path.join(cat_dir, f"{factor}.csv")
        if os.path.exists(src):
            shutil.move(src, dst)
            moved += 1
        else:
            not_found.append(factor)

# Step 3: Report
print(f"\nMoved: {moved} files")
if not_found:
    print(f"Not found ({len(not_found)}): {not_found}")

for cat in CLASSIFICATION:
    cat_dir = os.path.join(DST, cat)
    count = len(os.listdir(cat_dir))
    print(f"  {cat:40s}: {count:3d} files")

print(f"\nTotal: {moved} files organized into {len(CLASSIFICATION)} categories")