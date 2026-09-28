"""
STEP 5: Python deep-dive, and the export feeding STEP 6.

Usage:  python src/03_analysis.py

Writes to output/:
    fig_funnel.png        view -> cart -> purchase, item grain
    fig_cohort.png        week-1/2/3 return rate by first-seen week
    fig_price_band.png    abandonment vs price, split by failure mode
    fig_hour.png          abandonment by hour of day
    dashboard_cart_items.csv   flat table for Tableau / Power BI
    findings.md           headline, hypothesis tests and recommendations
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
# 1. THE FUNNEL: item grain
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
ax.set_title("Checkout funnel (item level)", weight="bold")
plt.tight_layout(); plt.savefig(f"{OUT}/fig_funnel.png"); plt.close()

# =====================================================================
# 2. WEEKLY COHORTS  (one month of data, so weekly, not monthly)
#    Cohort = the week a user was first seen. Week k = share of that
#    cohort active again k weeks later. Only the four complete weeks are
#    used; the last 3 days of the month are a partial week.
# =====================================================================
act = pd.read_sql("""
    SELECT s.user_id, s.session_start
    FROM dim_session s
    WHERE s.is_bot = 0
""", con)
act["day"] = pd.to_datetime(act["session_start"].astype(str).str.replace(" UTC", "", regex=False)).dt.normalize()
start = act["day"].min()
act["week"] = (act["day"] - start).dt.days // 7
act = act[act["week"] <= 3]
act["cohort"] = act.groupby("user_id")["week"].transform("min")
act["k"] = act["week"] - act["cohort"]
coh = act.groupby(["cohort", "k"])["user_id"].nunique().unstack()
retention = coh.divide(coh[0], axis=0) * 100
retention.index = [(start + pd.Timedelta(weeks=int(c))).strftime("%d %b") for c in retention.index]
wk = retention.drop(columns=0)
week1 = wk[1].dropna().mean() if 1 in wk.columns else float("nan")

fig, ax = plt.subplots(figsize=(6.5, 3.2))
ax.imshow(wk.values, cmap="Blues", aspect="auto", vmin=0, vmax=15)
ax.set_xticks(range(wk.shape[1]))
ax.set_xticklabels([f"Week {c}" for c in wk.columns])
ax.set_yticks(range(len(wk))); ax.set_yticklabels(wk.index)
for i in range(wk.shape[0]):
    for j in range(wk.shape[1]):
        v = wk.values[i, j]
        if not np.isnan(v):
            ax.text(j, i, f"{v:.1f}%", ha="center", va="center", fontsize=9,
                    color="white" if v > 10 else INK)
ax.set_title("Weekly cohorts: % of each week's new visitors who return",
             weight="bold", fontsize=10)
ax.set_xlabel("Weeks since first seen"); ax.set_ylabel("First seen (week of)")
plt.tight_layout(); plt.savefig(f"{OUT}/fig_cohort.png"); plt.close()
print("Cohort sizes:\n", coh[0].to_string())
print("Weekly return (%):\n", wk.round(1).to_string(), f"\nAverage week-1 return: {week1:.1f}%\n")

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
ax.set_title("Abandonment by price band, split by failure mode",
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
# 5. HYPOTHESIS TESTS: computed first, then judged
# =====================================================================
band_aband = 100 - pb.purchased
spread = band_aband.max() - band_aband.min()

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

findings = f"""# Findings: cart abandonment (generated by 03_analysis.py)

Computed over {len(ci):,} cart items ({ci.user_id.nunique():,} users, bots excluded).
Re-run rather than hand-edit.

## Headline
**{100*ci.abandoned.mean():.1f}% of items added to a cart are never purchased.**
Abandoned cart value {lost_value:,.0f} vs realised revenue {won_value:,.0f}, about
{lost_value/max(won_value,1):.1f}x. This sizes the problem; it is not a recovery target.

## Silent vs active abandonment
- Removed from cart (active rejection): **{split.get('removed',0):.1f}%**
- Silently abandoned (never revisited): **{split.get('abandoned_silent',0):.1f}%**

Removal means the customer looked again and said no. Silence means they never
came back to the basket. One blended rate hides that these need different fixes.

## Week-1 return
Average week-1 return rate across weekly cohorts: **{week1:.1f}%**.

## Hypotheses tested
| Hypothesis | Result |
|---|---|
| Price drives abandonment | {band_aband.min():.1f}% to {band_aband.max():.1f}% abandoned across price bands, a {spread:.1f}-point spread |
| First visits convert worse | {d3[0]:.1f}% abandoned on first visits vs {d3[1]:.1f}% on returning visits |
| Time of day drives abandonment | {d2[1]:.1f}% at {d2[0]:02d}:00 UTC vs {d2[3]:.1f}% at {d2[2]:02d}:00 UTC |

Timestamps are UTC and the shop is in Moscow (UTC+3), so time of day is not
treated as a driver without a timezone correction.

## Recommendations
1. Basket-recovery messaging aimed at silently abandoned items, not removed ones.
2. Persistent baskets, since few new visitors return on their own.
3. No discounting, as long as abandonment stays flat across price bands.

Validation: A/B test on the silent-abandonment cohort. Half receive a basket
reminder within 24 hours; measure purchase rate within 7 days, with unsubscribe
rate as a guardrail.
"""
open(f"{OUT}/findings.md", "w").write(findings)

# =====================================================================
# 6. DASHBOARD EXPORT: one flat table
# =====================================================================
dash = ci[["user_session", "product_id", "user_id", "carted_at", "price",
           "price_band", "outcome", "abandoned", "brand", "start_hour",
           "n_views", "n_carts", "cohort_month", "seconds_to_purchase"]].copy()
dash["cart_date"] = dash.carted_at.dt.date
dash["weekday"] = dash.carted_at.dt.day_name()
dash.to_csv(f"{OUT}/dashboard_cart_items.csv", index=False)

print(f"Headline abandonment: {100*ci.abandoned.mean():.1f}%")
print("Wrote:", ", ".join(sorted(f for f in os.listdir(OUT) if not f.endswith('.db'))))
