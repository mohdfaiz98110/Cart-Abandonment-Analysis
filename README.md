# Cart Abandonment Analysis — REES46 Cosmetics Shop

## Run it

```bash
python src/make_sample_data.py                    # fake data, ~20s
python src/01_load_and_model.py --source sample
python src/03_analysis.py
```

Then the real thing: Kaggle → *eCommerce Events History in Cosmetics Shop*
(publisher `mkechinov`). Drop the monthly CSVs into `data/raw/` and:

```bash
python src/01_load_and_model.py --source raw --months 2019-Oct
python src/03_analysis.py
```

**Start with one month.** Oct 2019 alone is ~4M events, plenty for every step
in the brief, and it keeps SQLite responsive. Add months by listing them:
`--months 2019-Oct 2019-Nov 2019-Dec`. You need at least three for the cohort
chart to say anything, and all five make it better.

SQL lives in `src/02_queries.sql`, run against `output/cosmetics.db`.

---

## The funnel is now literal

```
view  ──►  cart  ──►  purchase
             │
             └──►  remove_from_cart     (explicit exit)
```

No proxy, no caveat, no "the dataset doesn't track cart events so I defined…".
The events are in the data. This is the whole reason for switching.

### But get the grain right — this is the new interview question

| Grain | Question it answers | Abandonment (fixture) |
|---|---|---|
| Session | "did this visit convert?" | 81.5% |
| Cart item | "did this cart item convert?" | 87.7% |

A session that carts three items and buys one is **one win and two losses**.
Session grain scores it a win and hides the other two. Item grain is the honest
abandonment number; session grain is the right traffic number. `fact_cart_items`
is built at item grain for exactly this reason.

Have this ready: *"I reported abandonment at item grain because session grain
counts a partial conversion as a full one and understates the loss by about six
points. I computed both."*

### The distinction that is actually your edge

Almost every public notebook on this dataset reports one blended abandonment
rate. `fact_cart_items.outcome` splits it in three:

- `purchased`
- `removed` — they looked again and said no → **price / value / shipping objection**
- `abandoned_silent` — never came back to the cart → **friction / distraction / no reminder**

These need different fixes. A single blended rate hides the fact that you need
two interventions, not one. In the fixture the split is almost 50/50, which is
what makes the recommendation slide write itself.

---

## Real quirks the loader handles

1. **`event_time` ends in `" UTC"`.** It is a string, not a timestamp. Parsing
   the raw value is slow and can silently yield object dtype. The loader strips
   the suffix, then parses with an explicit format.
2. **`category_code` is mostly NULL** and `brand` often is too. Not an error —
   this store's catalogue is sparsely tagged. Kept as `unknown`, never dropped.
3. **Double-fired events.** Client-side logging emits the same action twice at
   the same second. Deduped on (time, type, product, user, session).
4. **Bot traffic.** Sessions with 100+ events and under 4s median dwell are
   flagged `is_bot=1` — **flagged, not deleted**, so the call stays reversible
   and every query states its own filter. Retail Rocket's own documentation
   warns browsing logs can run <sup>up to 40% abnormal</sup>; expect this to
   matter on the real data.
5. **Chunked loading** at 500k rows, so a 4M-row month never fully lands in
   memory.

---

## What you get back, and what you gave up

**Won:** a literal funnel (Step 3), a cohort chart with a readable pattern
(Step 5), and recommendations you can actually name (Step 7). On Olist the
retention heatmap was nearly empty because ~97% of customers never returned.
Here it has real numbers in it.

**Lost:** the ready-made 9-table schema. Replaced by `01_load_and_model.py`,
which builds a six-table star schema from the flat log:

```
fact_events      ──┬── dim_product ── dim_brand
                   ├── dim_category
                   ├── dim_session ── dim_user
fact_cart_items  ──┘
```

"I designed the dimensional model" beats "I loaded nine CSVs someone else
designed." The Step 1 learning goal survives the switch — you just do the
modelling instead of reading it.

**Also lost:** payment type, delivery time, review scores, geography. Those
Olist angles have no counterpart here. The replacements — price band, brand,
hour of day, session depth, visit number — are better suited to abandonment
anyway.

---

## ⚠ The fixture numbers are fake

`make_sample_data.py` bakes in four drivers on purpose (price, basket size,
late-night, first-visit) so you can watch the pipeline recover them. **Every
number in this README and in `output/findings.md` right now is an artefact of
that generator.** The real data will differ, possibly a lot, and the real
drivers may not be the four I invented.

Nothing goes on the deck or the resume until it came from `--source raw`.

---

## Resume bullets — now fillable

The brief's consulting bullet was *"Identified ~X% checkout drop-off on an
e-commerce funnel."* On Olist there was no X. Here there is:

- Analysed **20M+ user events** from a live e-commerce store; modelled a raw
  event log into a six-table star schema in SQL.
- Identified **X% cart abandonment**, and separated active rejection from
  silent abandonment — two failure modes requiring different interventions.
- Isolated 3 drivers via cohort and funnel analysis; recommended targeted fixes
  with an A/B validation plan.

Fill in the real X. Any figure you can't derive on the spot comes off.

---

## What's left

- **Step 6, dashboard.** `output/dashboard_cart_items.csv` is one flat table by
  design — Tableau Public handles a single source far better than joins. KPI
  row, funnel, one breakdown, one filter. Stop there.
- **Step 7, deck.** Wait for real numbers in `findings.md`.
