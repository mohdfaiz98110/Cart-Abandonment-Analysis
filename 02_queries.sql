-- =====================================================================
-- STEP 4 — SQL analysis.  Six queries, SQLite dialect.
-- Run one at a time:  sqlite3 output/olist.db < src/02_queries.sql
-- or open them individually while you read the results.
--
-- FUNNEL DEFINITION USED THROUGHOUT (memorise this, it is what gets asked):
--   placed    = every row in orders
--   approved  = order_approved_at IS NOT NULL   (payment cleared)
--   shipped   = order_delivered_carrier_date IS NOT NULL  (handed to carrier)
--   delivered = order_delivered_customer_date IS NOT NULL (customer received)
-- Drop-off at a stage = reached that stage but never reached the next.
-- =====================================================================


-- ---------------------------------------------------------------------
-- Q1. Overall funnel: how many orders survive each stage?
-- ---------------------------------------------------------------------
SELECT
    COUNT(*)                                                              AS placed,
    SUM(order_approved_at IS NOT NULL)                                    AS approved,
    SUM(order_delivered_carrier_date IS NOT NULL)                         AS shipped,
    SUM(order_delivered_customer_date IS NOT NULL)                        AS delivered,
    ROUND(100.0 * SUM(order_approved_at IS NOT NULL) / COUNT(*), 2)       AS pct_approved,
    ROUND(100.0 * SUM(order_delivered_customer_date IS NOT NULL) / COUNT(*), 2) AS pct_delivered
FROM orders;


-- ---------------------------------------------------------------------
-- Q2. Drop-off by product category.
--     Joins orders -> order_items -> products -> translation.
--     An order can hold several categories; this counts an order once per
--     distinct category it contains. Say that out loud if asked.
-- ---------------------------------------------------------------------
SELECT
    COALESCE(t.product_category_name_english, p.product_category_name) AS category,
    COUNT(DISTINCT o.order_id)                                          AS orders,
    COUNT(DISTINCT CASE WHEN o.order_delivered_customer_date IS NULL
                        THEN o.order_id END)                            AS not_delivered,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN o.order_delivered_customer_date IS NULL
                                      THEN o.order_id END)
                / COUNT(DISTINCT o.order_id), 2)                        AS pct_dropoff
FROM orders o
JOIN order_items i          ON o.order_id = i.order_id
JOIN products p             ON i.product_id = p.product_id
LEFT JOIN category_translation t ON p.product_category_name = t.product_category_name
GROUP BY category
HAVING orders >= 100          -- suppress tiny categories: 1 of 3 = 33% is noise
ORDER BY pct_dropoff DESC;


-- ---------------------------------------------------------------------
-- Q3. Drop-off by payment type and installment burden.
--     Tests the "payment friction" hypothesis.
-- ---------------------------------------------------------------------
SELECT
    pay.payment_type,
    CASE WHEN pay.payment_installments = 1  THEN '1 (paid in full)'
         WHEN pay.payment_installments <= 3 THEN '2-3'
         WHEN pay.payment_installments <= 6 THEN '4-6'
         ELSE '7+' END                                          AS installment_band,
    COUNT(*)                                                    AS orders,
    ROUND(AVG(pay.payment_value), 2)                            AS avg_order_value,
    ROUND(100.0 * SUM(o.order_delivered_customer_date IS NULL)
                / COUNT(*), 2)                                  AS pct_dropoff
FROM orders o
JOIN payments pay ON o.order_id = pay.order_id
GROUP BY pay.payment_type, installment_band
HAVING orders >= 50
ORDER BY pct_dropoff DESC;


-- ---------------------------------------------------------------------
-- Q4. Drop-off and delivery speed by customer state.
--     Ties geography to the delivery-time story.
-- ---------------------------------------------------------------------
SELECT
    c.customer_state,
    COUNT(*)                                                    AS orders,
    ROUND(100.0 * SUM(o.order_delivered_customer_date IS NULL)
                / COUNT(*), 2)                                  AS pct_dropoff,
    ROUND(AVG(JULIANDAY(o.order_delivered_customer_date)
            - JULIANDAY(o.order_purchase_timestamp)), 1)        AS avg_days_to_deliver,
    ROUND(AVG(JULIANDAY(o.order_delivered_customer_date)
            - JULIANDAY(o.order_estimated_delivery_date)), 1)   AS avg_days_vs_promise
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.customer_state
HAVING orders >= 100
ORDER BY avg_days_to_deliver DESC;


-- ---------------------------------------------------------------------
-- Q5. *** WINDOW FUNCTION *** First-time vs repeat customers.
--     ROW_NUMBER() numbers each person's orders in time order.
--     Critical: partition by customer_unique_id, NOT customer_id.
--     customer_id is regenerated per order in Olist — partitioning on it
--     makes every single customer look brand new. This is the #1 mistake
--     people make on this dataset.
-- ---------------------------------------------------------------------
WITH ranked AS (
    SELECT
        c.customer_unique_id,
        o.order_id,
        o.order_purchase_timestamp,
        o.order_delivered_customer_date,
        ROW_NUMBER() OVER (PARTITION BY c.customer_unique_id
                           ORDER BY o.order_purchase_timestamp) AS purchase_seq,
        COUNT(*)     OVER (PARTITION BY c.customer_unique_id)   AS lifetime_orders
    FROM orders o
    JOIN customers c ON o.customer_id = c.customer_id
)
SELECT
    CASE WHEN purchase_seq = 1 THEN '1st purchase'
         WHEN purchase_seq = 2 THEN '2nd purchase'
         ELSE '3rd+ purchase' END                               AS stage,
    COUNT(*)                                                    AS orders,
    COUNT(DISTINCT customer_unique_id)                          AS people,
    ROUND(100.0 * SUM(order_delivered_customer_date IS NULL)
                / COUNT(*), 2)                                  AS pct_dropoff
FROM ranked
GROUP BY stage
ORDER BY stage;


-- ---------------------------------------------------------------------
-- Q6. Does slow delivery predict bad reviews?
--     This is the bridge between the fulfilment story and the retention
--     story — it is what turns "we ship late" into "we lose customers".
-- ---------------------------------------------------------------------
SELECT
    CASE WHEN JULIANDAY(o.order_delivered_customer_date)
            - JULIANDAY(o.order_estimated_delivery_date) <= -10 THEN 'a. 10+ days early'
         WHEN JULIANDAY(o.order_delivered_customer_date)
            - JULIANDAY(o.order_estimated_delivery_date) <=  -3 THEN 'b. 3-9 days early'
         WHEN JULIANDAY(o.order_delivered_customer_date)
            - JULIANDAY(o.order_estimated_delivery_date) <=   0 THEN 'c. on time'
         WHEN JULIANDAY(o.order_delivered_customer_date)
            - JULIANDAY(o.order_estimated_delivery_date) <=   7 THEN 'd. up to 1 wk late'
         ELSE 'e. more than 1 wk late' END                      AS delivery_vs_promise,
    COUNT(*)                                                    AS orders,
    ROUND(AVG(r.review_score), 2)                               AS avg_review,
    ROUND(100.0 * SUM(r.review_score <= 2) / COUNT(*), 1)       AS pct_1_or_2_star
FROM orders o
JOIN reviews r ON o.order_id = r.order_id
WHERE o.order_delivered_customer_date IS NOT NULL
GROUP BY delivery_vs_promise
ORDER BY delivery_vs_promise;
