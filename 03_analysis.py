"""
STEP 5 — Python deep-dive, and the export that feeds STEP 6 (the dashboard).

Usage:  python src/03_analysis.py

Produces in output/:
    fig_funnel.png          stage-by-stage drop-off
    fig_cohort.png          retention heatmap by first-purchase month
    fig_delivery_review.png the delivery-lateness -> review-score relationship
    dashboard_orders.csv    ONE flat table for Tableau/Power BI (step 6)
    dashboard_funnel.csv    pre-aggregated funnel stages
    findings.md             the three drivers, with the numbers filled in
"""

import os
import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
con = sqlite3.connect(os.path.join(OUT, "olist.db"))
plt.rcParams.update({"figure.dpi": 130, "font.size": 10,
                     "axes.spines.top": False, "axes.spines.right": False})
INK, ACCENT, MUTED = "#1a2b3c", "#c1554a", "#9aa7b4"

# =====================================================================
# Build one wide analysis table. Everything below reads from this.
# =====================================================================
df = pd.read_sql("""
    SELECT
        o.order_id, o.order_status,
        o.order_purchase_timestamp, o.order_approved_at,
        o.order_delivered_carrier_date, o.order_delivered_customer_date,
        o.order_estimated_delivery_date,
        c.customer_unique_id, c.customer_state,
        p.payment_type, p.payment_installments, p.payment_value,
        r.review_score,
        COALESCE(t.product_category_name_english, pr.product_category_name) AS category
    FROM orders o
    JOIN customers c            ON o.customer_id = c.customer_id
    LEFT JOIN payments p        ON o.order_id = p.order_id AND p.payment_sequential = 1
    LEFT JOIN reviews r         ON o.order_id = r.order_id
    LEFT JOIN order_items i     ON o.order_id = i.order_id AND i.order_item_id = 1
    LEFT JOIN products pr       ON i.product_id = pr.product_id
    LEFT JOIN category_translation t ON pr.product_category_name = t.product_category_name
""", con)

for c in [c for c in df.columns if "timestamp" in c or "date" in c or c.endswith("_at")]:
    df[c] = pd.to_datetime(df[c], errors="coerce")

df["days_to_deliver"] = (df.order_delivered_customer_date
                         - df.order_purchase_timestamp).dt.days
df["days_vs_promise"] = (df.order_delivered_customer_date
                         - df.order_estimated_delivery_date).dt.days
df["completed"] = df.order_delivered_customer_date.notna()
df["cohort_month"] = df.groupby("customer_unique_id")["order_purchase_timestamp"] \
                       .transform("min").dt.to_period("M")
df["order_month"] = df.order_purchase_timestamp.dt.to_period("M")

print(f"Analysis table: {len(df):,} orders, {df.customer_unique_id.nunique():,} people\n")

# =====================================================================
# 1. THE FUNNEL
# =====================================================================
stages = {
    "Placed":    len(df),
    "Approved":  df.order_approved_at.notna().sum(),
    "Shipped":   df.order_delivered_carrier_date.notna().sum(),
    "Delivered": df.order_delivered_customer_date.notna().sum(),
}
funnel = pd.DataFrame({"stage": stages.keys(), "orders": stages.values()})
funnel["pct_of_placed"] = (100 * funnel.orders / len(df)).round(2)
funnel["lost_here"] = funnel.orders.shift(1).sub(funnel.orders).fillna(0).astype(int)
print(funnel.to_string(index=False), "\n")

fig, ax = plt.subplots(figsize=(7, 4))
bars = ax.bar(funnel.stage, funnel.orders, color=[INK, INK, INK, ACCENT], width=.6)
for b, row in zip(bars, funnel.itertuples()):
    ax.text(b.get_x() + b.get_width()/2, b.get_height(),
            f"{row.orders:,}\n{row.pct_of_placed:.1f}%", ha="center", va="bottom", fontsize=9)
    if row.lost_here:
        ax.text(b.get_x() + b.get_width()/2, b.get_height()*.5,
                f"−{row.lost_here:,}", ha="center", color="white", fontsize=9, weight="bold")
ax.set_ylim(0, funnel.orders.max()*1.18)
ax.set_ylabel("Orders"); ax.set_title("Fulfilment funnel — where orders stop", weight="bold")
ax.yaxis.set_major_formatter(lambda x, _: f"{x:,.0f}")
plt.tight_layout(); plt.savefig(f"{OUT}/fig_funnel.png"); plt.close()

# =====================================================================
# 2. COHORT RETENTION  (group by first-purchase month, track months since)
# =====================================================================
coh = df.dropna(subset=["cohort_month"]).copy()
coh["months_since"] = ((coh.order_month.dt.year - coh.cohort_month.dt.year) * 12
                       + (coh.order_month.dt.month - coh.cohort_month.dt.month))
piv = coh.pivot_table(index="cohort_month", columns="months_since",
                      values="customer_unique_id", aggfunc="nunique")
retention = piv.divide(piv[0], axis=0) * 100
retention = retention.iloc[:, :7]

fig, ax = plt.subplots(figsize=(8, max(3.5, .32*len(retention))))
im = ax.imshow(retention.values, cmap="Blues", aspect="auto", vmin=0, vmax=12)
ax.set_xticks(range(retention.shape[1]))
ax.set_xticklabels([f"M{c}" for c in retention.columns])
ax.set_yticks(range(len(retention)))
ax.set_yticklabels([str(i) for i in retention.index], fontsize=7)
for i in range(retention.shape[0]):
    for j in range(retention.shape[1]):
        v = retention.values[i, j]
        if not np.isnan(v):
            ax.text(j, i, f"{v:.0f}" if j else "100", ha="center", va="center",
                    fontsize=6.5, color="white" if v > 7 else INK)
ax.set_title("Cohort retention — % of each month's new customers who buy again",
             weight="bold", fontsize=10)
ax.set_xlabel("Months since first purchase")
plt.tight_layout(); plt.savefig(f"{OUT}/fig_cohort.png"); plt.close()

repeat_rate = 100 * (df.groupby("customer_unique_id").size() > 1).mean()
print(f"Customers who ever order twice: {repeat_rate:.1f}%\n")

# =====================================================================
# 3. DELIVERY LATENESS -> REVIEW SCORE
# =====================================================================
d = df.dropna(subset=["days_vs_promise", "review_score"]).copy()
bins = [-999, -10, -3, 0, 7, 999]
labels = ["10+ d early", "3-9 d early", "on time", "≤1 wk late", ">1 wk late"]
d["band"] = pd.cut(d.days_vs_promise, bins=bins, labels=labels)
by_band = d.groupby("band", observed=True).agg(
    orders=("order_id", "size"), avg_review=("review_score", "mean"),
    pct_bad=("review_score", lambda s: 100*(s <= 2).mean())).round(2)
print(by_band.to_string(), "\n")

fig, ax = plt.subplots(figsize=(7, 4))
ax.bar(by_band.index.astype(str), by_band.avg_review,
       color=[INK if x >= 4 else ACCENT for x in by_band.avg_review], width=.6)
for i, (v, n) in enumerate(zip(by_band.avg_review, by_band.orders)):
    ax.text(i, v, f"{v:.2f}\n(n={n:,})", ha="center", va="bottom", fontsize=8.5)
ax.set_ylim(0, 5.6); ax.set_ylabel("Average review score (1–5)")
ax.set_title("Late delivery is what breaks the review score", weight="bold")
plt.tight_layout(); plt.savefig(f"{OUT}/fig_delivery_review.png"); plt.close()

# =====================================================================
# 4. THE THREE DRIVERS — computed, not asserted
# =====================================================================
late = d[d.days_vs_promise > 0]
ontime = d[d.days_vs_promise <= 0]
driver1 = (ontime.review_score.mean(), late.review_score.mean(), len(late)/len(d)*100)

inst = df.dropna(subset=["payment_installments"])
hi = inst[inst.payment_installments >= 7]
lo = inst[inst.payment_installments == 1]
driver2 = (100*(1-lo.completed.mean()), 100*(1-hi.completed.mean()), len(hi)/len(inst)*100)

st = df.groupby("customer_state").agg(orders=("order_id", "size"),
                                      dropoff=("completed", lambda s: 100*(1-s.mean())),
                                      days=("days_to_deliver", "mean"))
st = st[st.orders >= max(50, .01*len(df))].sort_values("days", ascending=False)
driver3 = (st.index[0], st.days.iloc[0], st.days.iloc[-1], st.index[-1])

findings = f"""# Findings — the three drivers

Numbers generated by `03_analysis.py` on {len(df):,} orders.
Re-run after any change; never hand-edit these.

## Driver 1 — Delivery that misses the promised date
{driver1[2]:.1f}% of delivered orders arrive after the estimated delivery date.
Those orders average **{driver1[1]:.2f}/5** in reviews vs **{driver1[0]:.2f}/5** for on-time
orders — a {driver1[0]-driver1[1]:.2f}-point gap. Review score is the only
satisfaction signal in this dataset, and it collapses on lateness.

## Driver 2 — Payment friction at high installment counts
Orders split into 7+ installments fail to complete at **{driver2[1]:.2f}%**
vs **{driver2[0]:.2f}%** for single-payment orders. High-installment orders are
{driver2[2]:.1f}% of all orders, so this is not a rounding error.

## Driver 3 — Geography drives delivery time drives churn
{driver3[0]} averages **{driver3[1]:.1f} days** to deliver vs **{driver3[2]:.1f} days**
in {driver3[3]}. Slow states inherit Driver 1's review damage.

## The number that matters most
Only **{repeat_rate:.1f}%** of customers ever place a second order.
The fulfilment funnel leaks ~{100-funnel.pct_of_placed.iloc[-1]:.1f}% of orders.
The *retention* funnel leaks {100-repeat_rate:.1f}% of customers.
That second number is the real business problem, and Drivers 1–3 are why.
"""
open(f"{OUT}/findings.md", "w").write(findings)

# =====================================================================
# 5. EXPORT FOR THE DASHBOARD (step 6)
# =====================================================================
dash = df[["order_id", "order_status", "order_purchase_timestamp",
           "order_delivered_customer_date", "order_estimated_delivery_date",
           "customer_state", "category", "payment_type", "payment_installments",
           "payment_value", "review_score", "days_to_deliver", "days_vs_promise",
           "completed"]].copy()
dash["funnel_stage"] = np.select(
    [df.order_delivered_customer_date.notna(),
     df.order_delivered_carrier_date.notna(),
     df.order_approved_at.notna()],
    ["4. Delivered", "3. Shipped", "2. Approved"], default="1. Placed only")
dash.to_csv(f"{OUT}/dashboard_orders.csv", index=False)
funnel.to_csv(f"{OUT}/dashboard_funnel.csv", index=False)

print("Wrote:", ", ".join(sorted(f for f in os.listdir(OUT) if not f.endswith(".db"))))
