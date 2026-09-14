# XVI · Market Observatory

An observation-aware Polymarket archive explorer: **original fills → sorted DuckDB snapshot → exact per-market caches → interactive dashboard**.

This reconstructs **historical traded outcome prices**, not continuously executable quotes or calibrated probabilities. The dashboard is a review instrument, not a research-sample selector.

## Open the dashboard

Python 3.11 or newer is required. No frontend build, external chart CDN, API key, or Node runtime is needed to use it.

```bash
git clone https://github.com/parthchvn/Dashboard_.git
cd Dashboard_
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e .
python -m xvi serve --demo
```

Open **http://127.0.0.1:8000**. The first demo launch generates a small deterministic synthetic archive. Fictional markets have `demo-` IDs and a persistent **SYNTHETIC PREVIEW** banner. The demo is never presented as historical Polymarket data and uses a separate database.

## Use your real dataset

Download original `trades.parquet` and its matching `markets.parquet` from [SII-WANGZJ/Polymarket_data](https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data). Pin the dataset commit instead of a moving `main` revision. Keep the inputs: ingestion does not edit them. The large archive is **not included or automatically downloaded**.

```bash
# Replace the paths and revision with your actual downloaded snapshot.
python 01_ingest_polymarket.py \
  --trades data/raw/trades.parquet \
  --markets data/raw/markets.parquet \
  --source-revision YOUR_DATASET_COMMIT_SHA \
  --db data/polymarket.duckdb \
  --memory 4GB --threads 4

python -m xvi serve --db data/polymarket.duckdb
```

Search for an imported market ID, question, or event. Any market **in the local archive** can be opened. Entering a missing ID does not fetch its trades remotely. Market metadata is optional, but it supplies meaningful labels and event siblings.

Optional cache warming:

```bash
python 02_build_market_levels.py YOUR_MARKET_ID --level all
# Equivalent: python -m xvi build YOUR_MARKET_ID --level all
```

The first request otherwise builds only the raw cache and requested aggregation level. Later requests reuse them. Local Parquet globs and CSV files are supported. Full unclustered remote files are not rescanned on dashboard interactions.

**Storage and replacement:** allow substantial free space for original files, the database, sorting intermediates, and per-market caches. The memory limit does not cap disk use. Stop the server before deliberate re-ingestion with `--replace`; the serving process locks its snapshot against replacement. Full-archive ingestion and latency have not been benchmarked. Speed depends on market size, storage and hardware, especially on first access.

## Interface

- Searchable market/event library; light and dark themes; responsive desktop/mobile layouts; setup, empty, loading and error states.
- Zoomable traded-price chart with automatic resolution, a full-history navigator, drag and wheel zoom, two always-visible initial/final date cells (inclusive UTC days). Double-click or Home resets. Raw execution dots appear at raw resolution; high/low whiskers retain each occupied bar's extremes when zoomed out.
- Window-specific price changes, recorded notional, fill counts, distinct transaction counts, observed-slot coverage, volume bars, and a paginated execution tape. Select a fill to inspect both original and outcome-1-equivalent semantics.
- Event comparison includes every sibling in the imported metadata, including metadata-only markets without fills. A common-as-of sum reports stale/missing legs; it is a diagnostic, not an enforced identity or proof of an exhaustive outcome universe.
- Provenance is measured from ingested files. A closing outcome is a conservative **metadata indication**, not an on-chain settlement assertion. A missing settlement time is shown as unavailable; `end_date` is never silently treated as settlement time.
- CSV export contains actual fills in the current window, never visual carry-forward values. Copy view preserves a local market/window link.

## Date and wallet filters

Open a market, then use **Initial date** and **Final date** above the chart. Both are ordinary date-entry cells; no modal or preset buttons are required. **Apply filters** selects both complete dates in UTC, including the final day's last second. Selecting the same date twice selects one day. Internally, this is `[initial midnight, midnight after final date)`, independent of your computer's timezone and daylight-saving changes. Invalid or reversed dates leave the applied view unchanged.

The chart still supports drag/scroll zoom for second-level inspection. Its footer shows the exact window. After zooming, the cells show the days containing that window; editing a date selects whole days again. A wallet-only change preserves the exact zoomed window. **All dates** resets the time range without removing the applied wallet filter.

Enter up to **50 full wallet addresses** in **Wallet IDs**, separated by commas, whitespace, new lines, or semicolons. Choose **Maker or taker**, **Taker only**, or **Maker only**, then apply. Matching is exact and case-insensitive. Multiple addresses are ORed, duplicate addresses collapse, and a fill matching both sides is still counted once. Blank input means all wallets. Invalid addresses are rejected rather than silently ignored. ENS names and partial addresses are not resolved.

The filter affects the price chart, navigator, volume, window statistics, execution tape, pagination, and CSV export together. The chart is explicitly labeled **wallet-filtered**; it is not the full-market price path or a wallet's portfolio value. Price/action fields retain their existing recorded-taker interpretation, even for maker filters. Maker and taker address cells are visible in the tape; click either to apply that address and role, or use the inspector's wallet buttons. **Clear wallets** returns to market-wide observations without changing the applied time window.

Filtered bars are computed from matching raw fills, not from market-wide cached bars. Prior observations used for visual staleness must also match the wallet and role. Empty filtered windows remain empty. The shared market cache is never rewritten by a wallet query; existing databases need no re-ingestion. Wallet queries still scan the selected market's raw cache and are not separately wallet-indexed or full-archive benchmarked.

**Copy view** includes the applied market, dates, wallet addresses and role in the local URL fragment; do not share that URL when you wish to keep the chosen addresses private. Switching to a different market clears the wallet filter. Event comparison remains explicitly market-wide, and market-wide review overlays are hidden while a wallet subset is active. No new research sampling or trader-identity claims are introduced.

API endpoints `series`, `trades`, and `export.csv` accept the same optional query parameters:

```text
/api/markets/MARKET_ID/series?start=UNIX_SECONDS&end=UNIX_SECONDS&wallets=ADDRESS_1,ADDRESS_2&wallet_role=either
```

API timestamps remain `[start,end)` Unix seconds; only the date cells provide inclusive-day conversion.

## Chart granularity

The **Filter this market** row now contains **Initial date**, **Final date**, **Granularity**, **Wallet IDs**, and **Wallet role**. Set all of them and click **Apply filters** once.

Choose **1 minute**, **2 minutes**, **5 minutes**, **10 minutes**, **15 minutes**, **30 minutes**, **1 hour**, **4 hours**, or **1 day**. **Raw fills**, **30 seconds**, and zoom-aware **Auto** are also available. **Custom minutes…** accepts any whole number from **1 to 1440**, such as 3 or 7. Invalid intervals do not change the active view. Manual granularity stays fixed when zooming or changing wallet filters; **Auto** chooses the displayed resolution. Copy view and reload retain the applied interval.

Granularity groups the **price chart and volume bars**, not the trade table or CSV export. A 2-minute bar summarizes all matching raw fills in that interval, with exact OHLC, share-weighted VWAP, weighted median, distinct transaction count, and notional. It does not discard every other point or average the 1-minute medians. Full-window statistics stay identical when only granularity changes. Wallet filtering happens **before** aggregation.

Bins are aligned to Unix epoch UTC and labeled by their end, with left-closed intervals. For example, ordinary 2-minute bins are `[12:00,12:02)`, `[12:02,12:04)`. A custom interval that does not divide one day (such as 7 minutes) stays on that fixed epoch grid across midnight; it is not restarted at the selected initial date. Boundary bins only include the selected-window fills. Empty bins remain absent.

The API accepts `level=2m`, `level=7m`, etc. Equivalent preset aliases such as `60m` canonicalize to `1h`. Custom intervals without a preset are aggregated from the selected raw window on demand, without building another full-history cache. Existing cached intervals and imported databases need no migration. Manual requests over 6,000 observed bars are rejected with a clear message; choose a shorter date range, a coarser interval, or Auto. They are never silently downsampled. Optional review overlays are used only at their exact level; a missing custom-level overlay is shown as unavailable, not substituted with another detector's interval.

## Data semantics and safeguards

### Original trades only

Use **`trades.parquet`**, not the derived `quant.parquet` or user-level `users.parquet`.

Required columns:

```text
market_id, timestamp, block_number, log_index, transaction_hash, contract,
price, usd_amount, token_amount, nonusdc_side, taker_direction, maker, taker
```

Optional `event_id`, `condition_id`, and `asset_id` are preserved. Metadata uses `id`, `question`, `event_id`, `event_title`, `answer1`, `answer2`, `closed`, `outcome_prices`, and `end_date`; an explicitly supplied `resolution_timestamp` is also supported. Both JSON and the card's Python-style two-element outcome-price strings are parsed safely.

**Normalization happens once.** `token1` retains its original price; `token2` maps to `1 - price` and reverses taker direction. Original token side, price, addresses and cash notional stay available. Outcome 1 is labeled from metadata, not assumed to be named YES. Already-normalized columns and user-level schemas are rejected. Renaming a derived input can defeat schema detection; supplying the original source is still required.

**Validation is explicit.** Invalid prices, amounts, sides and non-integral chain/time identities are removed and counted in the manifest. Exact duplicate chain logs collapse; conflicting records with the same `(contract, transaction_hash, log_index)` abort ingestion. Known exchange counterparties are excluded from displayed `trades`, but remain in canonical `fills`. This address rule is not a bot/human classifier. Recorded-fill notional is not audited economic turnover.

**Time stays honest.** Fills are ordered by market, timestamp, block number, log index, transaction hash and contract. No subsecond timing is invented. Empty intervals never enter analytical tables. The renderer carries an observation only with increasing staleness: solid until the selected threshold, dashed/faded until four times the threshold, then absent. Previous-window observations are returned separately from actual observations in the selected window. Raw fills, not visual carry values, feed all statistics and reviews.

**Aggregation is exact.** Cached presets are `raw`, `30s`, `1m`, `2m`, `5m`, `10m`, `15m`, `30m`, `1h`, `4h`, and `1d`. Custom whole-minute chart intervals from `1m` to `1440m` are also supported. Each occupied left-closed `[bin_start, bin_end)` has OHLC, share-weighted VWAP, lower share-weighted median, original cash notional, share volume, fill count, distinct transaction count, and first/last trade times. Every level is recomputed from raw fills, not from averages or medians of smaller bars. Clipped boundary bars include only selected-window fills and retain their nominal bin-end label.

**Snapshots and caches are reproducible.** Ingestion builds a separate database and publishes it atomically. Snapshot-namespaced caches prevent reuse after re-ingestion; per-market locks and atomic renames prevent half-written cache files. Manifests record file names, sizes, modification times and supplied revision, not expensive full-file hashes. The original files remain your source of truth.

The API refuses oversized manual requests instead of silently dropping observations: raw/bar responses are capped at 6,000 rows. Auto chooses a coarser level. CSV export is capped at 100,000 fills; narrow the window or query DuckDB directly for larger exports. The execution tape is paginated independently of chart resolution.

## Optional existing `news_attr` review overlays

There is **no new movement detector**. The adapter imports the existing `polymarket_context/fine.py` from a **trusted local checkout** and calls its `FineConfig`, `make_fine_bars`, and `detect_fine` functions on full observed market history.

```bash
pip install -e '.[review]'
python -m xvi review YOUR_MARKET_ID \
  --news-attr ../news_attr --level 30s
```

Build other levels explicitly as needed. Raw chart views use the 30-second overlay. Each JSON sidecar records the source-code hash and detector configuration fingerprint. The inherited detector thresholds are not newly calibrated for each level. This adapter imports Python code, so do not point it at an untrusted checkout. Its full-market pandas workload is separate from the DuckDB ingestion path and can require substantial memory.

`detector_selects_research_samples: false` remains explicit in provenance, series, and review output. This project neither selects research samples nor performs news attribution. Metadata, trade notional and review annotations should not be mistaken for causally valid human-decision labels.

## Tests

```bash
pip install -e '.[test,review]'
python -m pytest -q
node --check xvi/static/app.js
node --check xvi/static/chart.js
node tests/chart.test.cjs
node tests/filters.test.cjs
```

Tests cover price/action normalization, safe IDs, duplicate conflicts, exact weighted aggregation, distinct transactions, chain ordering, interval boundaries, gaps, as-of sibling coverage, snapshot locks, cache invalidation, SQL-safe search, CSV export, response limits, review-adapter wiring, wallet role/OR filters, cache isolation, inclusive UTC dates, and filtered chart/tape/export consistency.

GitHub Actions runs integration tests on Python 3.11 and 3.13, plus **real DuckDB + HTTP + Chromium** smoke tests using clearly synthetic fixtures. Browser screenshots are retained as a workflow artifact. These tests do not download the full dataset. To run browser checks locally:

```bash
pip install playwright
playwright install chromium
python tests/browser_smoke.py
```

## Layout

```text
01_ingest_polymarket.py       convenient ingestion entry point
02_build_market_levels.py    convenient cache builder
xvi/data.py                  canonical snapshots and exact cached levels
xvi/app.py                   read-only FastAPI endpoints and CSV export
xvi/review.py                optional existing-detector bridge
xvi/static/                  self-contained interface and Canvas chart
xvi/demo.py                   isolated, deterministic fictional data
tests/                       domain, database/API, chart and browser checks
```

**Deployment:** the server binds to loopback by default and has no built-in authentication. Do not expose it publicly without an authenticated reverse proxy and suitable resource limits. This repository is application source, **not an already-hosted website**. Data, caches, environments and credentials are ignored by Git.

References: [original dataset schema](https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data), [DuckDB Parquet](https://duckdb.org/docs/stable/data/parquet/overview), [DuckDB zonemaps](https://duckdb.org/docs/stable/guides/performance/indexing).
