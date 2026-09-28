"""
STEP 1 + 2: load the REES46 event log and MODEL it into a star schema.

The raw download is one flat CSV per month. That is a fact table with the
dimensions denormalised into it. This script reverses that: it builds a proper
star schema, which is what makes steps 4-5 tractable.

Usage:
    python src/01_load_and_model.py --source sample
    python src/01_load_and_model.py --source raw --months 2019-Oct 2019-Nov

Schema produced in output/cosmetics.db:

    fact_events        one row per raw event          (grain: event)
    fact_cart_items    one row per product carted     (grain: session x product)
                       <- THIS is the cart-abandonment table
    dim_product        product_id, category_id, brand, median price
    dim_category       category_id, category_code, parsed level_1/2/3
    dim_user           user_id, first_seen, cohort_month, session count
    dim_session        user_session, user_id, start/end, event counts, is_bot

Reads in chunks so a 4M-row month never lands in memory all at once.
"""

import argparse
import os
import sqlite3
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
CHUNK = 500_000
log = []


def note(k, v):
    log.append((k, v))
    print(f"  [clean] {k}: {v}")


def load_events(con, src_dir, months):
    """Stream the monthly CSVs into fact_events, cleaning as we go."""
    con.execute("DROP TABLE IF EXISTS fact_events")
    total_in = total_out = 0

    for month in months:
        path = os.path.join(src_dir, f"{month}.csv")
        if not os.path.exists(path):
            raise SystemExit(f"Missing {path}")

        for chunk in pd.read_csv(path, chunksize=CHUNK):
            total_in += len(chunk)

            # THE GOTCHA: event_time is a string ending in " UTC".
            # pd.to_datetime on the raw string is slow and can silently produce
            # object dtype. Strip the suffix first, then parse.
            chunk["event_time"] = pd.to_datetime(
                chunk["event_time"].str.replace(" UTC", "", regex=False),
                format="%Y-%m-%d %H:%M:%S", errors="coerce", utc=True)

            chunk = chunk.dropna(subset=["event_time"])

            # Client-side logging double-fires: identical user+product+type at
            # the same second is one action, not two.
            before = len(chunk)
            chunk = chunk.drop_duplicates(
                subset=["event_time", "event_type", "product_id",
                        "user_id", "user_session"])
            if len(chunk) < before:
                note(f"{month}: dropped double-fired events",
                     f"{before - len(chunk):,} identical (time, type, product, user, session)")

            # Sessionless events cannot be placed in a funnel.
            b = len(chunk)
            chunk = chunk.dropna(subset=["user_session"])
            if len(chunk) < b:
                note(f"{month}: dropped events with NULL user_session",
                     f"{b - len(chunk):,}: a funnel needs a session to belong to")

            b = len(chunk)
            chunk = chunk[chunk["price"] > 0]
            if len(chunk) < b:
                note(f"{month}: dropped price <= 0",
                     f"{b - len(chunk):,} rows: cannot compute abandoned cart value from them")

            chunk["event_time"] = chunk["event_time"].dt.tz_localize(None)
            chunk.to_sql("fact_events", con, if_exists="append", index=False)
            total_out += len(chunk)

        print(f"  loaded {month}")

    note("overall row retention",
         f"{total_out:,} of {total_in:,} raw events kept ({total_out/total_in:.2%})")
    return total_out


def build_dimensions(con):
    """Derive the dimension tables from the fact table, in SQL, no pandas."""

    con.executescript("""
    DROP TABLE IF EXISTS dim_category;
    CREATE TABLE dim_category AS
    SELECT
        category_id,
        MAX(category_code)                                        AS category_code,
        -- category_code is a dotted path: 'appliances.personal.hair_cutter'
        CASE WHEN MAX(category_code) IS NULL THEN 'unknown'
             ELSE SUBSTR(MAX(category_code), 1,
                  CASE WHEN INSTR(MAX(category_code),'.')=0 THEN LENGTH(MAX(category_code))
                       ELSE INSTR(MAX(category_code),'.')-1 END) END AS level_1,
        COUNT(*)                                                  AS events
    FROM fact_events GROUP BY category_id;

    DROP TABLE IF EXISTS dim_brand;
    CREATE TABLE dim_brand AS
    SELECT COALESCE(brand,'unknown') AS brand,
           COUNT(DISTINCT product_id) AS products,
           COUNT(*) AS events
    FROM fact_events GROUP BY COALESCE(brand,'unknown');

    DROP TABLE IF EXISTS dim_product;
    CREATE TABLE dim_product AS
    SELECT product_id,
           MAX(category_id)           AS category_id,
           COALESCE(MAX(brand),'unknown') AS brand,
           ROUND(AVG(price),2)        AS avg_price,
           MIN(price)                 AS min_price,
           MAX(price)                 AS max_price,
           COUNT(*)                   AS events
    FROM fact_events GROUP BY product_id;

    DROP TABLE IF EXISTS dim_session;
    CREATE TABLE dim_session AS
    SELECT user_session,
           MAX(user_id)                                   AS user_id,
           MIN(event_time)                                AS session_start,
           MAX(event_time)                                AS session_end,
           COUNT(*)                                       AS n_events,
           SUM(event_type='view')                         AS n_views,
           SUM(event_type='cart')                         AS n_carts,
           SUM(event_type='remove_from_cart')             AS n_removes,
           SUM(event_type='purchase')                     AS n_purchases,
           CAST(strftime('%H', MIN(event_time)) AS INT)   AS start_hour
    FROM fact_events GROUP BY user_session;

    DROP TABLE IF EXISTS dim_user;
    CREATE TABLE dim_user AS
    SELECT user_id,
           MIN(event_time)                        AS first_seen,
           MAX(event_time)                        AS last_seen,
           strftime('%Y-%m', MIN(event_time))     AS cohort_month,
           COUNT(DISTINCT user_session)           AS n_sessions,
           SUM(event_type='purchase')             AS n_purchase_events
    FROM fact_events GROUP BY user_id;
    """)

    # --- bot / abnormal-traffic flag -------------------------------------
    # Rule: 100+ events in the session AND an average gap of under 4 seconds
    # between consecutive events.
    con.executescript("""
    ALTER TABLE dim_session ADD COLUMN is_bot INTEGER DEFAULT 0;
    UPDATE dim_session SET is_bot = 1
    WHERE n_events >= 100
      AND (JULIANDAY(session_end)-JULIANDAY(session_start))*86400.0
           / MAX(n_events-1,1) < 4.0;
    """)
    n_bot, ev_bot = con.execute(
        "SELECT COUNT(*), SUM(n_events) FROM dim_session WHERE is_bot=1").fetchone()
    note("flagged bot sessions",
         f"{n_bot:,} sessions / {ev_bot or 0:,} events: FLAGGED not deleted, so the "
         "decision stays reversible and every downstream query states its own filter")


def build_cart_items(con):
    """
    fact_cart_items: the cart-abandonment table.

    Grain: one row per (session, product) that was ever added to a cart.
    This grain matters. A session that carts 3 items and buys 1 is NOT a
    'converted session': it is 1 conversion and 2 abandonments. Aggregating
    at session level hides two thirds of the loss.

    outcome:
      purchased          carted and bought in the same session
      removed            carted then explicitly removed  -> active rejection
      abandoned_silent   carted, never removed, never bought -> passive drop
    """
    con.executescript("""
    DROP TABLE IF EXISTS fact_cart_items;
    CREATE TABLE fact_cart_items AS
    WITH carts AS (
        SELECT user_session, product_id, MAX(user_id) AS user_id,
               MIN(event_time) AS carted_at, AVG(price) AS price
        FROM fact_events WHERE event_type='cart'
        GROUP BY user_session, product_id
    ),
    removes AS (
        SELECT user_session, product_id, MIN(event_time) AS removed_at
        FROM fact_events WHERE event_type='remove_from_cart'
        GROUP BY user_session, product_id
    ),
    buys AS (
        SELECT user_session, product_id, MIN(event_time) AS purchased_at
        FROM fact_events WHERE event_type='purchase'
        GROUP BY user_session, product_id
    )
    SELECT
        c.user_session, c.product_id, c.user_id, c.carted_at,
        ROUND(c.price,2) AS price,
        r.removed_at, b.purchased_at,
        CASE WHEN b.purchased_at IS NOT NULL THEN 'purchased'
             WHEN r.removed_at   IS NOT NULL THEN 'removed'
             ELSE 'abandoned_silent' END AS outcome,
        CAST((JULIANDAY(b.purchased_at)-JULIANDAY(c.carted_at))*86400 AS INT)
             AS seconds_to_purchase
    FROM carts c
    LEFT JOIN removes r ON c.user_session=r.user_session AND c.product_id=r.product_id
    LEFT JOIN buys    b ON c.user_session=b.user_session AND c.product_id=b.product_id;
    """)


def main(source, months):
    src_dir = os.path.join(ROOT, "data", source)
    out_dir = os.path.join(ROOT, "output")
    os.makedirs(out_dir, exist_ok=True)
    db = os.path.join(out_dir, "cosmetics.db")
    if os.path.exists(db):
        os.remove(db)

    if not months:
        months = sorted(f[:-4] for f in os.listdir(src_dir) if f.endswith(".csv"))
    print(f"\nSource: {src_dir}\nMonths: {', '.join(months)}\n" + "-"*62)

    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")

    load_events(con, src_dir, months)

    print("\nIndexing")
    for s in ["CREATE INDEX ix_ev_sess ON fact_events(user_session)",
              "CREATE INDEX ix_ev_type ON fact_events(event_type)",
              "CREATE INDEX ix_ev_user ON fact_events(user_id)",
              "CREATE INDEX ix_ev_prod ON fact_events(product_id)"]:
        con.execute(s)

    print("\nBuilding dimensions")
    build_dimensions(con)
    print("Building fact_cart_items")
    build_cart_items(con)
    con.execute("CREATE INDEX ix_ci_sess ON fact_cart_items(user_session)")
    con.execute("CREATE INDEX ix_ci_out ON fact_cart_items(outcome)")
    con.commit()

    print("\n" + "-"*62 + "\nTables")
    for (t,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<20} {n:>10,}")

    print("\nHeadline funnel (bots excluded)")
    q = """SELECT
             (SELECT COUNT(DISTINCT user_session) FROM fact_events e
                JOIN dim_session s USING(user_session)
               WHERE s.is_bot=0) AS sessions,
             (SELECT COUNT(DISTINCT e.user_session) FROM fact_events e
                JOIN dim_session s USING(user_session)
               WHERE e.event_type='cart' AND s.is_bot=0) AS with_cart,
             (SELECT COUNT(DISTINCT e.user_session) FROM fact_events e
                JOIN dim_session s USING(user_session)
               WHERE e.event_type='purchase' AND s.is_bot=0) AS with_purchase"""
    s_, c_, p_ = con.execute(q).fetchone()
    print(f"  sessions {s_:,} -> with cart {c_:,} ({c_/s_:.1%}) "
          f"-> with purchase {p_:,} ({p_/max(c_,1):.1%} of carting sessions)")
    print(f"  SESSION-LEVEL CART ABANDONMENT: {1-p_/max(c_,1):.1%}")

    ci = pd.read_sql("SELECT outcome, COUNT(*) n FROM fact_cart_items "
                     "GROUP BY outcome ORDER BY n DESC", con)
    tot = ci.n.sum()
    print("\nItem-level outcomes (the honest grain)")
    for r in ci.itertuples():
        print(f"  {r.outcome:<20} {r.n:>9,}  {r.n/tot:>6.1%}")
    print(f"  ITEM-LEVEL CART ABANDONMENT: "
          f"{ci[ci.outcome!='purchased'].n.sum()/tot:.1%}")

    con.close()
    with open(os.path.join(out_dir, "cleaning_log.md"), "w") as f:
        f.write("# Cleaning decisions\n\n")
        for k, v in log:
            f.write(f"- **{k}**: {v}\n")
    print(f"\nDB  -> {db}\nLog -> {out_dir}/cleaning_log.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="sample", choices=["sample", "raw"])
    ap.add_argument("--months", nargs="*", default=None)
    a = ap.parse_args()
    main(a.source, a.months)
