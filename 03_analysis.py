"""
STEP 5 — Python deep-dive, and the export feeding STEP 6.

Usage:  python src/03_analysis.py

Writes to output/:
    fig_funnel.png        view -> cart -> purchase, item grain
    fig_cohort.png        retention by first-seen month  (works on this data)
    fig_price_band.png    abandonment vs price, split by failure mode
    fig_hour.png          abandonment by hour of day
    dashboard_cart_items.csv   flat table for Tableau / Power BI
    findings.md           the three drivers, numbers computed not asserted
"""

import os, sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
con = sqlite3.connect(os.path.join(OUT, "cosmetics.db"))
plt.rcParams.update({"figure.dpi": 130, "font.size": 10,
                     "axes.spines.top": False, "axes.spines.right": False})
INK, WARM, COOL, MUTED = "#1d2d44", "#c0563f", "#4a7c96", "#b6bfc9"

# =====================================================================
# Load the two working tables
# =====================================================================
ci = pd.read_sql("""
    SELECT ci.*, s.start_hour, s.n_views, s.n_carts, s.is_bot,
           p.brand, p.category_id, u.cohort_month
    FROM fact_cart_items ci
    JOIN dim_session s USING(user_session)
    JOIN dim_product p USING(product_id)
    JOIN dim_user    u ON ci.user_id = u.user_id
    WHERE s.is_bot = 0
""", con)
ci["carted_at"] = pd.to_datetime(ci["carted_at"])
ci["abandoned"] = ci.outcome != "purchased"

funnel_counts = pd.read_sql("""
    SELECT event_type, COUNT(*) n, COUNT(DISTINCT e.user_session) sessions
    FROM fact_events e JOIN dim_session s USING(user_session)
    WHERE s.is_bot=0 GROUP BY event_type
""", con).set_index("event_type")

print(f"Cart items analysed: {len(ci):,}  "
      f"({ci.user_id.nunique():,} users, {ci.user_session.nunique():,} sessions)\n")

# =====================================================================
# 1. THE FUNNEL — item grain
# =====================================================================
views = int(funnel_counts.loc["view", "n"])
carts = len(ci)
buys = int((~ci.abandoned).sum())
stages = pd.DataFrame({"stage": ["Viewed", "Added to cart", "Purchased"],
                       "n": [views, carts, buys]})
stages["pct_prev"] = (100 * stages.n / stages.n.shift(1)).round(1)
print(stages.to_string(index=False), "\n")

fig, ax = plt.subplots(figsize=(7, 4.2))
bars = ax.bar(stages.stage, stages.n, color=[MUTED, COOL, INK], width=.58)
for i, (b, r) in enumerate(zip(bars, stages.itertuples())):
    lbl = f"{r.n:,}" + ("" if i == 0 else f"\n{r.pct_prev:.1f}% of previous")
    ax.text(b.get_x()+b.get_width()/2, b.get_height(), lbl,
            ha="center", va="bottom", fontsize=9)
ax.set_ylim(0, stages.n.max()*1.22); ax.set_ylabel("Events")
ax.yaxis.set_major_formatter(lambda x, _: f"{x:,.0f}")
ax.set_title("Checkout funnel — a real one, not a proxy", weight="bold")
plt.tight_layout(); plt.savefig(f"{OUT}/fig_funnel.png"); plt.close()

# =====================================================================
# 2. COHORT RETENTION  — the analysis Olist could not support
# =====================================================================
act = pd.read_sql("""
    SELECT u.cohort_month,
           strftime('%Y-%m', s.session_start) AS active_month,
           COUNT(DISTINCT u.user_id) AS users
    FROM dim_user u JOIN dim_session s ON u.user_id = s.user_id
    WHERE s.is_bot = 0
    GROUP BY u.cohort_month, active_month
""", con)
piv = act.pivot(index="cohort_month", columns="active_month", values="users")
months = sorted(set(piv.index) | set(piv.columns))
piv = piv.reindex(index=months, columns=months)
offset = pd.DataFrame(
    {i: [piv.iloc[r, months.index(piv.index[r]) + i]
         if months.index(piv.index[r]) + i < len(months) else np.nan
         for r in range(len(piv))] for i in range(len(months))},
    index=piv.index)
retention = offset.divide(offset[0], axis=0) * 100

fig, ax = plt.subplots(figsize=(6.5, 3.2))
im = ax.imshow(retention.values, cmap="Blues", aspect="auto", vmin=0, vmax=60)
ax.set_xticks(range(retention.shape[1]))
ax.set_xticklabels([f"M{c}" for c in retention.columns])
ax.set_yticks(range(len(retention))); ax.set_yticklabels(retention.index)
for i in range(retention.shape[0]):
    for j in range(retention.shape[1]):
        v = retention.values[i, j]
        if not np.isnan(v):
            ax.text(j, i, f"{v:.0f}%", ha="center", va="center", fontsize=9,
                    color="white" if v > 35 else INK)
ax.set_title("Cohort retention — % of each month's new users still active",
             weight="bold", fontsize=10)
ax.set_xlabel("Months since first seen")
plt.tight_layout(); plt.savefig(f"{OUT}/fig_cohort.png"); plt.close()
print("Retention triangle:\n", retention.round(1).to_string(), "\n")

# =====================================================================
# 3. PRICE BAND, split by failure mode
# =====================================================================
bands = [0, 5, 10, 20, 40, 1e9]
labels = ["<5", "5-10", "10-20", "20-40", "40+"]
ci["price_band"] = pd.cut(ci.price, bands, labels=labels, right=False)
pb = ci.groupby("price_band", observed=True).agg(
    items=("product_id", "size"),
    purchased=("outcome", lambda s: (s == "purchased").mean()*100),
    removed=("outcome", lambda s: (s == "removed").mean()*100),
    silent=("outcome", lambda s: (s == "abandoned_silent").mean()*100))
pb = pb[pb["items"] >= 50]

fig, ax = plt.subplots(figsize=(7.2, 4.2))
x = np.arange(len(pb))
ax.bar(x, pb.purchased, color=INK, label="Purchased", width=.62)
ax.bar(x, pb.removed, bottom=pb.purchased, color=WARM,
       label="Removed (active rejection)", width=.62)
ax.bar(x, pb.silent, bottom=pb.purchased+pb.removed, color=MUTED,
       label="Silently abandoned", width=.62)
ax.set_xticks(x); ax.set_xticklabels(pb.index)
ax.set_ylabel("% of cart items"); ax.set_xlabel("Item price")
ax.set_title("Two different failure modes, and price moves them differently",
             weight="bold")
ax.legend(fontsize=8.5, frameon=False, ncol=3, loc="lower center",
          bbox_to_anchor=(.5, -.28))
plt.tight_layout(); plt.savefig(f"{OUT}/fig_price_band.png"); plt.close()
print(pb.round(1).to_string(), "\n")

# =====================================================================
# 4. HOUR OF DAY
# =====================================================================
hr = ci.groupby("start_hour").agg(items=("product_id", "size"),
                                  aband=("abandoned", lambda s: 100*s.mean()))
hr = hr[hr["items"] >= 100]
fig, ax = plt.subplots(figsize=(7.5, 3.4))
ax.bar(hr.index, hr.aband,
       color=[WARM if v >= hr.aband.mean()+2 else COOL for v in hr.aband], width=.7)
ax.axhline(hr.aband.mean(), color=INK, ls="--", lw=1)
ax.text(hr.index.min(), hr.aband.mean()+.4, f"mean {hr.aband.mean():.1f}%", fontsize=8)
ax.set_xlabel("Session start hour (UTC)"); ax.set_ylabel("% abandoned")
ax.set_ylim(max(0, hr.aband.min()-4), hr.aband.max()+2)
ax.set_title("When carts get abandoned", weight="bold")
plt.tight_layout(); plt.savefig(f"{OUT}/fig_hour.png"); plt.close()

# =====================================================================
# 5. THE THREE DRIVERS — computed
# =====================================================================
lo = ci[ci.price < 10]; hi = ci[ci.price >= 10]
d1 = (100*lo.abandoned.mean(), 100*hi.abandoned.mean(), len(hi)/len(ci)*100)

peak = hr.aband.idxmax(); trough = hr.aband.idxmin()
d2 = (peak, hr.aband.max(), trough, hr.aband.min())

vis = pd.read_sql("""
    WITH seq AS (SELECT user_session, user_id,
                 ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY session_start) rn
                 FROM dim_session WHERE is_bot=0)
    SELECT CASE WHEN rn=1 THEN 'first' ELSE 'returning' END grp,
           AVG(CASE WHEN ci.outcome<>'purchased' THEN 1.0 ELSE 0 END)*100 pct
    FROM seq JOIN fact_cart_items ci USING(user_session) GROUP BY grp
""", con).set_index("grp").pct
d3 = (vis.get("first", np.nan), vis.get("returning", np.nan))

split = ci.outcome.value_counts(normalize=True)*100
lost_value = ci.loc[ci.abandoned, "price"].sum()
won_value = ci.loc[~ci.abandoned, "price"].sum()

findings = f"""# Findings — cart abandonment

Computed by `03_analysis.py` over {len(ci):,} cart items
({ci.user_id.nunique():,} users, bots excluded). Re-run rather than hand-edit.

## Headline
**{100*ci.abandoned.mean():.1f}% of items added to a cart are never purchased.**
Abandoned cart value {lost_value:,.0f} vs realised revenue {won_value:,.0f} —
roughly {lost_value/max(won_value,1):.1f}x the revenue actually captured is
sitting in abandoned carts.

## The split almost nobody makes
- Removed from cart (active rejection): **{split.get('removed',0):.1f}%**
- Silently abandoned (never revisited):  **{split.get('abandoned_silent',0):.1f}%**

These are different problems. Removal means they reconsidered and said no —
that is a price, value or shipping-cost objection. Silence means they never
came back — that is friction, distraction, or a missing reminder. One fix
does not address both, and reporting a single blended abandonment rate hides
the fact that you need two.

## Driver 1 — Price
Items under 10 abandon at **{d1[0]:.1f}%**; items at 10+ abandon at **{d1[1]:.1f}%**
({d1[2]:.0f}% of cart items). Price also shifts the *mode* of failure:
expensive items are rejected explicitly, cheap ones drift away silently.

## Driver 2 — Time of day
Abandonment peaks at hour **{d2[0]:02d}:00 UTC ({d2[1]:.1f}%)** and bottoms at
**{d2[2]:02d}:00 ({d2[3]:.1f}%)** — a {d2[1]-d2[3]:.1f}-point spread. Late sessions
are browse-and-drift sessions.

## Driver 3 — Visit experience
First-ever visits abandon at **{d3[0]:.1f}%** vs **{d3[1]:.1f}%** for returning
visits. Familiarity converts; the first visit is where the funnel leaks worst.

## Recommendations these support
1. Cart-recovery email timed to the silent-abandonment cohort, fired the
   morning after a late-night session.
2. Shipping-cost / total transparency shown at add-to-cart, not at checkout —
   targets the removal cohort in the higher price bands.
3. First-visit incentive, since first visits are measurably the weakest step.
Each maps to a driver, and each is A/B testable.
"""
open(f"{OUT}/findings.md", "w").write(findings)

# =====================================================================
# 6. DASHBOARD EXPORT — one flat table
# =====================================================================
dash = ci[["user_session", "product_id", "user_id", "carted_at", "price",
           "price_band", "outcome", "abandoned", "brand", "start_hour",
           "n_views", "n_carts", "cohort_month", "seconds_to_purchase"]].copy()
dash["cart_date"] = dash.carted_at.dt.date
dash["weekday"] = dash.carted_at.dt.day_name()
dash.to_csv(f"{OUT}/dashboard_cart_items.csv", index=False)

print(f"Headline abandonment: {100*ci.abandoned.mean():.1f}%")
print("Wrote:", ", ".join(sorted(f for f in os.listdir(OUT) if not f.endswith('.db'))))
