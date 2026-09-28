-- =====================================================================
-- STEP 4 — SQL analysis.  Six queries against output/cosmetics.db
--
-- FUNNEL DEFINITION (this is now literal, not a proxy):
--   view  ->  cart  ->  purchase,   with remove_from_cart as an explicit exit.
--
-- GRAIN WARNING, say this out loud in an interview:
--   Session grain answers "did this visit convert?"
--   Item grain (fact_cart_items) answers "did this cart item convert?"
--   A session carting 3 items and buying 1 is 1 win and 2 losses. Session
--   grain scores it a win and hides the other two. Item grain is the honest
--   number for abandonment; session grain is the right number for traffic.
--
-- Every query filters s.is_bot = 0. Bots are flagged, never deleted.
-- =====================================================================


-- ---------------------------------------------------------------------
-- Q1. The funnel, both grains, side by side.
-- ---------------------------------------------------------------------
WITH clean AS (
    SELECT e.* FROM fact_events e
    JOIN dim_session s USING(user_session) WHERE s.is_bot = 0
)
SELECT
    'session' AS grain,
    COUNT(DISTINCT user_session)                                       AS reached_view,
    COUNT(DISTINCT CASE WHEN event_type='cart'     THEN user_session END) AS reached_cart,
    COUNT(DISTINCT CASE WHEN event_type='purchase' THEN user_session END) AS reached_purchase,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN event_type='purchase' THEN user_session END)
                / COUNT(DISTINCT CASE WHEN event_type='cart' THEN user_session END), 2)
                                                                       AS pct_cart_to_purchase
FROM clean
UNION ALL
SELECT
    'cart item',
    NULL,
    COUNT(*),
    SUM(outcome = 'purchased'),
    ROUND(100.0 * SUM(outcome='purchased') / COUNT(*), 2)
FROM fact_cart_items ci
JOIN dim_session s USING(user_session) WHERE s.is_bot = 0;


-- ---------------------------------------------------------------------
-- Q2. Abandonment by price band.  Tests the sticker-shock hypothesis.
-- ---------------------------------------------------------------------
SELECT
    CASE WHEN ci.price <   5 THEN 'a. under 5'
         WHEN ci.price <  10 THEN 'b. 5-10'
         WHEN ci.price <  20 THEN 'c. 10-20'
         WHEN ci.price <  40 THEN 'd. 20-40'
         ELSE                     'e. 40+' END                 AS price_band,
    COUNT(*)                                                   AS cart_items,
    ROUND(100.0*SUM(ci.outcome='purchased')       /COUNT(*),2) AS pct_purchased,
    ROUND(100.0*SUM(ci.outcome='removed')         /COUNT(*),2) AS pct_removed,
    ROUND(100.0*SUM(ci.outcome='abandoned_silent')/COUNT(*),2) AS pct_silent,
    ROUND(SUM(CASE WHEN ci.outcome<>'purchased' THEN ci.price END),0) AS abandoned_value
FROM fact_cart_items ci
JOIN dim_session s USING(user_session)
WHERE s.is_bot = 0
GROUP BY price_band
ORDER BY price_band;


-- ---------------------------------------------------------------------
-- Q3. Abandonment by brand — joins the product and brand dimensions.
--     Only brands with real volume; small denominators are noise.
-- ---------------------------------------------------------------------
SELECT
    p.brand,
    COUNT(*)                                                   AS cart_items,
    ROUND(AVG(ci.price),2)                                     AS avg_price,
    ROUND(100.0*SUM(ci.outcome<>'purchased')/COUNT(*),2)       AS pct_abandoned,
    ROUND(SUM(CASE WHEN ci.outcome<>'purchased' THEN ci.price END),0) AS abandoned_value
FROM fact_cart_items ci
JOIN dim_product p USING(product_id)
JOIN dim_session s USING(user_session)
WHERE s.is_bot = 0 AND p.brand <> 'unknown'
GROUP BY p.brand
HAVING cart_items >= 200
ORDER BY abandoned_value DESC
LIMIT 15;


-- ---------------------------------------------------------------------
-- Q4. Abandonment by hour of day.  Drives a concrete scheduling
--     recommendation (when to fire the recovery email).
-- ---------------------------------------------------------------------
SELECT
    s.start_hour                                               AS hour_utc,
    COUNT(*)                                                   AS cart_items,
    ROUND(100.0*SUM(ci.outcome<>'purchased')/COUNT(*),2)       AS pct_abandoned
FROM fact_cart_items ci
JOIN dim_session s USING(user_session)
WHERE s.is_bot = 0
GROUP BY s.start_hour
HAVING cart_items >= 100
ORDER BY pct_abandoned DESC;


-- ---------------------------------------------------------------------
-- Q5. *** WINDOW FUNCTIONS *** Does a customer improve with experience?
--     ROW_NUMBER ranks each user's sessions in time order, so we can ask
--     whether abandonment falls as someone gets familiar with the shop.
--     This is the retention story the Olist data could not support.
-- ---------------------------------------------------------------------
WITH seq AS (
    SELECT
        s.user_id,
        s.user_session,
        s.session_start,
        ROW_NUMBER() OVER (PARTITION BY s.user_id ORDER BY s.session_start) AS session_no,
        COUNT(*)     OVER (PARTITION BY s.user_id)                          AS lifetime_sessions
    FROM dim_session s
    WHERE s.is_bot = 0
)
SELECT
    CASE WHEN session_no = 1 THEN '1st visit'
         WHEN session_no = 2 THEN '2nd visit'
         WHEN session_no <= 5 THEN '3rd-5th visit'
         ELSE '6th+ visit' END                                  AS visit_stage,
    COUNT(DISTINCT seq.user_session)                            AS sessions,
    COUNT(ci.product_id)                                        AS cart_items,
    ROUND(100.0*SUM(ci.outcome<>'purchased')/COUNT(ci.product_id),2) AS pct_abandoned
FROM seq
JOIN fact_cart_items ci USING(user_session)
GROUP BY visit_stage
ORDER BY visit_stage;


-- ---------------------------------------------------------------------
-- Q6. Explicit rejection vs silent abandonment — and how fast the
--     winners convert.  These two failure modes need DIFFERENT fixes:
--       removed  -> they looked at it again and said no  (price/value)
--       silent   -> they never came back to the cart     (friction/reminder)
--     Almost nobody using this dataset separates them. That is your edge.
-- ---------------------------------------------------------------------
SELECT
    ci.outcome,
    COUNT(*)                                                   AS cart_items,
    ROUND(100.0*COUNT(*) / SUM(COUNT(*)) OVER (), 2)           AS pct_of_all,
    ROUND(AVG(ci.price),2)                                     AS avg_price,
    ROUND(AVG(ci.seconds_to_purchase)/60.0,1)                  AS avg_mins_to_buy,
    ROUND(SUM(ci.price),0)                                     AS total_value
FROM fact_cart_items ci
JOIN dim_session s USING(user_session)
WHERE s.is_bot = 0
GROUP BY ci.outcome
ORDER BY cart_items DESC;
