"""
Generates synthetic data with the EXACT schema of the REES46 cosmetics CSVs:
    event_time, event_type, product_id, category_id, category_code,
    brand, price, user_id, user_session

Real quirks reproduced on purpose:
  - event_time is a string ending in " UTC"  <- real parsing gotcha
  - category_code is mostly NULL (~65%)
  - brand is often NULL (~40%)
  - duplicate events (double-fired client-side logging)
  - bot sessions with absurd event counts
  - occasional price = 0.0
  - a few NULL user_session

Usage:  python src/make_sample_data.py
Writes: data/sample/2019-Oct.csv, 2019-Nov.csv, 2019-Dec.csv
"""

import os
import numpy as np
import pandas as pd

rng = np.random.default_rng(7)
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "sample")
os.makedirs(OUT, exist_ok=True)

N_USERS = 9000
N_PRODUCTS = 1200
MONTHS = [("2019-Oct", "2019-10-01", 31),
          ("2019-Nov", "2019-11-01", 30),
          ("2019-Dec", "2019-12-01", 31)]

# Real brand names from this dataset (Russian beauty-supply store)
BRANDS = ["runail", "irisk", "grattol", "masura", "estel", "jessnail", "ingarden",
          "kapous", "bpw.style", "uno", "milv", "freedecor", "severina", "concept",
          "cnd", "haruyama", "lianail", "oniq", "patrisa", "nagaraku", "kaypro",
          "levissime", "marathon", "zinger", "italwax"]

# category_code in this dataset is sparse and oddly shaped — that is authentic
CATEGORY_CODES = [
    "appliances.personal.hair_cutter", "accessories.bag", "apparel.glove",
    "furniture.bathroom.bath", "stationery.cartrige", "sport.diving",
    "appliances.environment.vacuum",
]

# ---------------------------------------------------------------- products
product_ids = rng.integers(5_000_000, 5_999_999, N_PRODUCTS)
category_ids = rng.choice(
    rng.integers(1_487_580_000_000_000_000, 1_487_580_100_000_000_000, 60), N_PRODUCTS)
prod_brand = rng.choice(BRANDS + [None] * 17, N_PRODUCTS)          # ~40% null
prod_price = np.round(rng.gamma(1.9, 4.2, N_PRODUCTS) + 0.5, 2)     # right-skewed

cat_code_map = {}
for cid in np.unique(category_ids):
    cat_code_map[cid] = rng.choice(CATEGORY_CODES) if rng.random() < 0.35 else None

products = pd.DataFrame({
    "product_id": product_ids,
    "category_id": category_ids,
    "brand": prod_brand,
    "price": prod_price,
})

# ------------------------------------------------- users, cohorts, sessions
user_ids = rng.integers(400_000_000, 599_999_999, N_USERS)
# staggered acquisition so cohort retention is measurable
user_first_month = rng.choice([0, 1, 2], N_USERS, p=[0.55, 0.28, 0.17])
# beauty is a repeat category: decent but not universal return rate
user_loyalty = rng.beta(1.4, 3.2, N_USERS)

rows = []
event_seq = 0

for m_idx, (label, start, ndays) in enumerate(MONTHS):
    month_start = pd.Timestamp(start)
    active = np.where(
        (user_first_month <= m_idx) &
        ((user_first_month == m_idx) | (rng.random(N_USERS) < user_loyalty))
    )[0]

    for u in active:
        uid = user_ids[u]
        n_sessions = 1 + rng.poisson(0.6 + 1.8 * user_loyalty[u])
        is_new_user = (user_first_month[u] == m_idx)

        for s in range(n_sessions):
            sess = f"{rng.integers(0, 16**8):08x}-{rng.integers(0, 16**4):04x}-" \
                   f"{rng.integers(0, 16**4):04x}-{rng.integers(0, 16**12):012x}"
            day = rng.integers(0, ndays)
            # bimodal daily rhythm: lunchtime and late evening
            hour = int(rng.choice([rng.integers(9, 14), rng.integers(19, 24)]))
            t = month_start + pd.Timedelta(days=int(day), hours=hour,
                                           minutes=int(rng.integers(0, 60)))

            # --- bot session: huge, fast, never converts -------------------
            if rng.random() < 0.004:
                for _ in range(int(rng.integers(180, 400))):
                    p = rng.integers(0, N_PRODUCTS)
                    t += pd.Timedelta(seconds=int(rng.integers(1, 3)))
                    rows.append((t, "view", products.product_id[p],
                                 products.category_id[p], products.brand[p],
                                 products.price[p], uid, sess))
                continue

            # --- normal session --------------------------------------------
            n_views = 1 + rng.poisson(2.6)
            viewed = rng.choice(N_PRODUCTS, size=min(n_views, N_PRODUCTS), replace=False)
            carted = []

            for p in viewed:
                t += pd.Timedelta(seconds=int(rng.integers(8, 240)))
                price = float(products.price[p])
                rows.append((t, "view", products.product_id[p],
                             products.category_id[p], products.brand[p], price, uid, sess))

                # DRIVER 1: pricier items get carted less
                p_cart = np.clip(0.62 - 0.020 * price, 0.10, 0.62)
                if rng.random() < p_cart:
                    t += pd.Timedelta(seconds=int(rng.integers(3, 40)))
                    rows.append((t, "cart", products.product_id[p],
                                 products.category_id[p], products.brand[p],
                                 price, uid, sess))
                    carted.append((p, price))

            if not carted:
                continue

            cart_value = sum(pr for _, pr in carted)
            # DRIVER 2: big baskets get abandoned (sticker shock at total)
            # DRIVER 3: late-night sessions convert worse
            # DRIVER 4: first-time users convert worse
            p_buy = 0.62
            p_buy -= 0.016 * cart_value
            p_buy -= 0.13 if hour >= 21 else 0.0
            p_buy -= 0.14 if is_new_user else 0.0
            p_buy = float(np.clip(p_buy, 0.04, 0.80))

            converts = rng.random() < p_buy

            for p, price in carted:
                # explicit removal: driven by price, happens whether or not they buy
                if rng.random() < np.clip(0.30 + 0.020 * price, 0.20, 0.70):
                    t += pd.Timedelta(seconds=int(rng.integers(10, 300)))
                    rows.append((t, "remove_from_cart", products.product_id[p],
                                 products.category_id[p], products.brand[p],
                                 price, uid, sess))
                    continue
                if converts and rng.random() < 0.92:
                    t += pd.Timedelta(seconds=int(rng.integers(20, 900)))
                    rows.append((t, "purchase", products.product_id[p],
                                 products.category_id[p], products.brand[p],
                                 price, uid, sess))

    df = pd.DataFrame(rows, columns=["event_time", "event_type", "product_id",
                                     "category_id", "brand", "price",
                                     "user_id", "user_session"])
    rows = []

    df = df.sort_values("event_time").reset_index(drop=True)

    # --- inject authentic dirt -------------------------------------------
    dup = df.sample(frac=0.011, random_state=m_idx)          # double-fired events
    df = pd.concat([df, dup]).sort_values("event_time").reset_index(drop=True)
    df.loc[df.sample(frac=0.0015, random_state=m_idx).index, "user_session"] = np.nan
    df.loc[df.sample(frac=0.0008, random_state=m_idx).index, "price"] = 0.0

    df["category_code"] = df["category_id"].map(cat_code_map)
    # the real gotcha: timestamps are strings with a trailing " UTC"
    df["event_time"] = df["event_time"].dt.strftime("%Y-%m-%d %H:%M:%S") + " UTC"

    df = df[["event_time", "event_type", "product_id", "category_id",
             "category_code", "brand", "price", "user_id", "user_session"]]
    path = os.path.join(OUT, f"{label}.csv")
    df.to_csv(path, index=False)
    print(f"  {label}.csv  {len(df):>9,} events   "
          f"({df.event_type.value_counts(normalize=True).mul(100).round(1).to_dict()})")

print(f"\nSample data written to {os.path.abspath(OUT)}")
