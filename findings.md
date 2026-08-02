# Findings — Cart Abandonment Analysis

**Dataset:** REES46 cosmetics shop, October 2019
**Scope:** 3,882,509 events · 399,366 users · 871,605 sessions · 979,851 cart items
**Bots excluded.** 94.6% of raw rows retained after cleaning.

---

## Headline

**87.5% of items added to a cart are never purchased.**

The funnel:

| Stage | Events | Conversion |
|---|---|---|
| Viewed | 1,856,584 | — |
| Added to cart | 979,851 | 52.8% of views |
| Purchased | 122,952 | 12.5% of cart items |

Half of everyone who views a product adds it to a basket. Then seven out of
eight of those baskets are abandoned. The leak is not at the top of the
funnel — it is at the very bottom, after intent has already been shown.

Abandoned cart value is **7.2× realised revenue** (4,620,090 vs 638,715).

> Note: this is a measure of scale, not a recovery target. Some carting is
> browsing behaviour with no purchase intent. It sizes the problem; it does
> not promise the money is retrievable.

---

## Finding 1 — Silent abandonment is 3.3× active rejection

| Outcome | Share of cart items |
|---|---|
| Silently abandoned (never revisited) | **67.2%** |
| Removed from cart (active rejection) | **20.2%** |
| Purchased | 12.5% |

This is the most important result, and it comes from separating two things
that most analyses report as one blended number.

**Removal** means the customer looked at their basket again and decided
against it — a price, value or delivery objection.
**Silence** means they never returned to the basket at all — distraction,
friction, or simply forgetting.

These require different interventions. A discount aimed at the silent 67%
is aimed at people who never got far enough to see it.

---

## Finding 2 — 90% of new visitors never return

Weekly cohorts within October:

| First seen | New users | Week 1 | Week 2 | Week 3 |
|---|---|---|---|---|
| 01 Oct | 137,349 | 9.8% | 7.3% | 6.3% |
| 08 Oct | 84,115 | 9.9% | 6.7% | — |
| 15 Oct | 75,614 | 9.3% | — | — |
| 22 Oct | 71,435 | — | — | — |

**Average week-1 return rate: 9.7%.** Near-identical across every cohort,
so this is structural, not a bad week.

The decline flattens after week one (9.8 → 7.3 → 6.3), meaning a small loyal
core does exist. It is roughly 6 in 100.

**This connects to Finding 1.** Silent abandoners are not deliberating and
returning later — they are leaving and not coming back. The abandoned cart
and the lost customer are largely the same event.

---

## Finding 3 — Price does not drive abandonment

Tested and **rejected**. Abandonment by price band:

| Price band | Cart items | Abandoned |
|---|---|---|
| under 5 | 680,316 | 87.3% |
| 5–10 | 197,031 | 88.4% |
| 10–20 | 74,992 | 86.2% |
| 20–40 | 16,860 | 88.1% |
| 40+ | 10,652 | 87.5% |

A 2.2-point spread across a **10× price range**, and not monotonic — the
10–20 band performs best. Items under 5 and items over 40 abandon at
effectively the same rate.

**This is a finding, not a null.** It rules out the most intuitive
explanation and removes discounting from the recommendation set. Any
proposal to fix abandonment with price cuts is not supported by this data.

---

## Tested and not supported

Recorded so the analysis cannot be accused of only reporting what worked.

| Hypothesis | Result | Verdict |
|---|---|---|
| Price drives abandonment | 87.3%–88.4% across bands | Rejected — 2.2 pts, non-monotonic |
| First visits convert worse | 86.5% first vs 88.3% returning | Rejected — and reversed |
| Time of day drives abandonment | 91.2% at 00:00 UTC vs 85.3% at 06:00 | Weak, and confounded |

The visit-experience result runs **opposite** to the expected direction:
returning visitors abandon slightly more than first-timers. Plausibly
returning users browse more casually. Either way, the effect is under two
points and not actionable.

The time-of-day spread (5.9 points) is the largest of the three but
timestamps are **UTC** while the shop is Russian (Moscow, UTC+3). The
apparent midnight peak is 03:00 local — an overnight-browsing effect, not an
evening one. Not reported as a driver without timezone correction.

---

## Recommendations

Each addresses a finding that survived testing.

**1. Cart-recovery messaging aimed at the silent 67%.**
Not a discount. A reminder that the basket exists. These customers never
rejected anything — they left. Target the 67.2% silent cohort, not the 20.2%
who actively removed items.

**2. Persistent baskets and a return path.**
With a 9.7% week-one return rate, a basket that survives the session is the
only remaining link to a customer who otherwise will not be seen again.

**3. Do not discount.**
Abandonment is flat across a 10× price range. Price cuts would sacrifice
margin against a factor the data shows is not driving the behaviour.

### Validation

A/B test on recommendation 1: split the silent-abandonment cohort, send a
basket reminder to one half within 24 hours, measure the difference in
purchase rate within 7 days. Primary metric: cart-item conversion. Guardrail:
unsubscribe rate.

---

## Limitations

1. **One month only.** October 2019. No seasonality, no long-run retention.
2. **Weekly cohorts, not monthly.** A one-month window collapses monthly
   cohorts to a single cell. Weekly gives four groups but only measures
   short-run return — and beauty repurchase cycles are longer than a week.
   "Week-1 return rate", never "retention".
3. **First cohort is contaminated.** Everyone active in week 1 counts as
   "new" because the data starts there, including pre-existing customers.
   Its 137,349 users overstate genuine acquisition.
4. **Bot detection is conservative.** Flagged 50 sessions / 10,275 events —
   0.26%. Likely an undercount. Threshold needs testing before the figure
   is trusted.
5. **`category_code` is mostly null**, so category-level breakdowns are
   unreliable. Brand is the usable dimension.
6. **Carting is not intent.** Some users cart to compare or save for later.
   Abandonment overstates lost sales by an unmeasurable amount.
