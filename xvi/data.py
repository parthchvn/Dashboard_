"""Atomic archive ingestion and exact observed-bin caches.

Archive-sized operations stay in DuckDB; inputs are never modified. Stop the
server before replacing a snapshot. One database is one source snapshot.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path
import uuid

from filelock import FileLock
from .domain import EXCHANGES, LEVELS, SCHEMA_VERSION, choose_level, epoch, identifier, metadata_outcome

LOG = logging.getLogger(__name__)
ORDER = "timestamp, block_number, log_index, transaction_hash, contract"
DESC = ", ".join(x + " DESC" for x in ORDER.split(", "))
REQUIRED = {"market_id", "timestamp", "block_number", "log_index", "transaction_hash", "contract",
            "price", "usd_amount", "token_amount", "nonusdc_side", "taker_direction", "maker", "taker"}


def duckdb():
    import duckdb as module
    return module


def literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def rows(con, query: str, params: list | None = None) -> list[dict]:
    cursor = con.execute(query, params or [])
    names = [x[0] for x in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def source_reader(paths: list[str]) -> tuple[str, list[dict]]:
    files = sorted({str(Path(p).resolve()) for pattern in paths for p in glob.glob(pattern)})
    if not files or any(not Path(p).is_file() for p in files):
        raise ValueError("No local files matched. Supply files or a quoted *.parquet glob, not a URL/directory.")
    arguments = "[" + ",".join(literal(p) for p in files) + "]"
    suffixes = {Path(p).suffix.lower() for p in files}
    if suffixes == {".parquet"}:
        reader = f"read_parquet({arguments}, union_by_name=true)"
    elif suffixes == {".csv"}:
        reader = f"read_csv({arguments}, header=true, all_varchar=true)"
    else:
        raise ValueError("Files must be uniformly original-trade Parquet or CSV.")
    manifest = [{"name": Path(p).name, "bytes": Path(p).stat().st_size,
                 "mtime_ns": Path(p).stat().st_mtime_ns} for p in files]
    return reader, manifest


def ingest(paths: list[str], db_path: str | Path, markets: str | None = None, *,
           replace: bool = False, memory: str = "4GB", threads: int = 4,
           revision: str = "unspecified", demo: bool = False,
           exclude_addresses: tuple[str, ...] = EXCHANGES) -> dict:
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not 1 <= threads <= 64:
        raise ValueError("threads must be between 1 and 64")
    with FileLock(str(db_path) + ".snapshot.lock", timeout=1):
        if db_path.exists() and not replace:
            raise FileExistsError(f"{db_path} exists. Stop the server and use --replace deliberately.")
        temp = db_path.with_name(db_path.name + ".building-" + uuid.uuid4().hex)
        con = duckdb().connect(str(temp), config={"memory_limit": memory, "threads": threads})
        try:
            con.execute("SET preserve_insertion_order=false")
            reader, source_files = source_reader(paths)
            columns = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {reader}").fetchall()}
            if columns & {"user", "role", "yes_price", "price_outcome1", "price_observed"}:
                raise ValueError("User-level or already-normalized input rejected. Use original trades.parquet.")
            if REQUIRED - columns:
                raise ValueError(f"Missing original-trade columns: {sorted(REQUIRED - columns)}. Do not use quant.parquet.")
            optional = lambda name: f"CAST({name} AS VARCHAR)" if name in columns else "NULL::VARCHAR"
            LOG.info("Reading original fills; validating source semantics")
            con.execute(f"""CREATE TABLE stage AS SELECT
                trim(CAST(market_id AS VARCHAR)) AS market_id,
                {optional('event_id')} AS event_id, {optional('condition_id')} AS condition_id,
                {optional('asset_id')} AS asset_id,
                try_cast(timestamp AS BIGINT) AS timestamp,
                try_cast(block_number AS BIGINT) AS block_number,
                try_cast(log_index AS BIGINT) AS log_index,
                lower(trim(CAST(transaction_hash AS VARCHAR))) AS transaction_hash,
                lower(trim(CAST(contract AS VARCHAR))) AS contract,
                lower(trim(CAST(maker AS VARCHAR))) AS maker,
                lower(trim(CAST(taker AS VARCHAR))) AS taker,
                try_cast(price AS DOUBLE) AS raw_price,
                try_cast(usd_amount AS DOUBLE) AS usd_amount,
                try_cast(token_amount AS DOUBLE) AS token_amount,
                CAST(nonusdc_side AS VARCHAR) AS nonusdc_side,
                upper(CAST(taker_direction AS VARCHAR)) AS taker_direction,
                (try_cast(timestamp AS DOUBLE)=try_cast(timestamp AS BIGINT)
                 AND try_cast(block_number AS DOUBLE)=try_cast(block_number AS BIGINT)
                 AND try_cast(log_index AS DOUBLE)=try_cast(log_index AS BIGINT)) AS integer_identity
                FROM {reader}""")
            input_count = con.execute("SELECT count(*) FROM stage").fetchone()[0]
            valid = """coalesce(integer_identity AND
                regexp_full_match(market_id,'[A-Za-z0-9][A-Za-z0-9_-]{0,127}') AND
                timestamp BETWEEN 0 AND 4102444800 AND block_number>=0 AND log_index>=0 AND
                length(transaction_hash)>0 AND length(contract)>0 AND length(maker)>0 AND length(taker)>0 AND
                isfinite(raw_price) AND raw_price BETWEEN 0 AND 1 AND
                isfinite(token_amount) AND token_amount>0 AND isfinite(usd_amount) AND usd_amount>=0 AND
                nonusdc_side IN ('token1','token2') AND taker_direction IN ('BUY','SELL'),false)"""
            invalid = con.execute(f"SELECT count(*) FROM stage WHERE NOT ({valid})").fetchone()[0]
            con.execute(f"CREATE TABLE unique_fills AS SELECT DISTINCT * EXCLUDE(integer_identity) FROM stage WHERE {valid}")
            unique_count = con.execute("SELECT count(*) FROM unique_fills").fetchone()[0]
            conflict = con.execute("""SELECT contract,transaction_hash,log_index FROM unique_fills
                GROUP BY ALL HAVING count(*)>1 LIMIT 1""").fetchone()
            if conflict:
                raise ValueError(f"Conflicting records share a chain-log ID: {conflict}")
            if not unique_count:
                raise ValueError("No valid fills remain. Inspect the source schema.")
            addresses = ",".join(literal(x.lower()) for x in exclude_addresses)
            excluded = f"maker IN ({addresses}) OR taker IN ({addresses})" if addresses else "false"
            LOG.info("Writing sorted canonical fills")
            con.execute(f"""CREATE TABLE fills AS SELECT *,
                CASE nonusdc_side WHEN 'token1' THEN raw_price ELSE 1-raw_price END AS yes_price,
                CASE WHEN nonusdc_side='token1' THEN taker_direction
                     WHEN taker_direction='BUY' THEN 'SELL' ELSE 'BUY' END AS yes_direction,
                ({excluded}) AS excluded_exchange
                FROM unique_fills ORDER BY market_id, {ORDER}""")
            con.execute("CREATE VIEW trades AS SELECT * EXCLUDE(excluded_exchange) FROM fills WHERE NOT excluded_exchange")
            included = con.execute("SELECT count(*) FROM trades").fetchone()[0]
            if not included:
                raise ValueError("All valid fills were excluded exchange-counterparty records.")
            if con.execute("""SELECT market_id FROM trades GROUP BY market_id
                HAVING count(DISTINCT nullif(event_id,''))>1 LIMIT 1""").fetchone():
                raise ValueError("A market has conflicting event IDs in its fills.")
            fields = ["question", "event_id", "event_title", "answer1", "answer2", "closed",
                      "outcome_prices", "end_date", "resolution_timestamp"]
            metadata_files = []
            if markets:
                mr, metadata_files = source_reader([markets])
                mc = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {mr}").fetchall()}
                if "id" not in mc:
                    raise ValueError("Market metadata requires its original id column.")
                expressions = [f"CAST({f} AS VARCHAR) AS {f}" if f in mc else f"NULL::VARCHAR AS {f}" for f in fields]
                con.execute(f"CREATE TABLE metadata AS SELECT trim(CAST(id AS VARCHAR)) AS id,{','.join(expressions)} FROM {mr}")
                if con.execute("SELECT id FROM metadata GROUP BY id HAVING count(*)>1 LIMIT 1").fetchone():
                    raise ValueError("Duplicate metadata IDs: provide one consistent metadata snapshot.")
                if con.execute("SELECT id FROM metadata WHERE NOT coalesce(regexp_full_match(id,'[A-Za-z0-9][A-Za-z0-9_-]{0,127}'),false) LIMIT 1").fetchone():
                    raise ValueError("Invalid market metadata ID.")
            else:
                con.execute("CREATE TABLE metadata (id VARCHAR," + ",".join(f + " VARCHAR" for f in fields) + ")")
            LOG.info("Computing measured archive coverage and market directory")
            con.execute(f"""CREATE TABLE summaries AS SELECT market_id,max(event_id) AS trade_event_id,
                min(timestamp) AS first_ts,max(timestamp) AS last_ts,count(*) AS fill_count,
                count(DISTINCT transaction_hash) AS tx_count,sum(usd_amount) AS notional,
                first(yes_price ORDER BY {ORDER}) AS first_price,last(yes_price ORDER BY {ORDER}) AS last_price,
                min(yes_price) AS low,max(yes_price) AS high FROM trades GROUP BY market_id""")
            if con.execute("""SELECT s.market_id FROM summaries s JOIN metadata m ON s.market_id=m.id
                WHERE nullif(s.trade_event_id,'') IS NOT NULL AND nullif(m.event_id,'') IS NOT NULL
                AND s.trade_event_id<>m.event_id LIMIT 1""").fetchone():
                raise ValueError("Metadata and fill event IDs disagree.")
            con.execute("""CREATE TABLE markets AS SELECT coalesce(s.market_id,m.id) AS market_id,
                coalesce(nullif(m.question,''),'Market '||coalesce(s.market_id,m.id)) AS question,
                coalesce(nullif(m.event_id,''),nullif(s.trade_event_id,'')) AS event_id,
                coalesce(nullif(m.event_title,''),'Unlabelled event') AS event_title,
                coalesce(nullif(m.answer1,''),'Outcome 1') AS answer1,
                coalesce(nullif(m.answer2,''),'Outcome 2') AS answer2,
                m.closed,m.outcome_prices,m.end_date,m.resolution_timestamp,
                s.first_ts,s.last_ts,coalesce(s.fill_count,0) AS fill_count,
                coalesce(s.tx_count,0) AS tx_count,coalesce(s.notional,0) AS notional,
                s.first_price,s.last_price,s.low,s.high
                FROM summaries s FULL OUTER JOIN metadata m ON s.market_id=m.id""")
            coverage = con.execute("SELECT min(first_ts),max(last_ts),count(*) FROM summaries").fetchone()
            audit = dict(schema_version=SCHEMA_VERSION,snapshot=uuid.uuid4().hex,demo=demo,
                source_kind="original_trades",source_revision=revision,source_files=source_files,
                metadata_files=metadata_files,input_rows=input_count,invalid_rows_removed=invalid,
                duplicate_logs_removed=input_count-invalid-unique_count,
                exchange_rows_excluded=unique_count-included,displayed_fills=included,
                market_count=coverage[2],first_ts=coverage[0],last_ts=coverage[1],
                excluded_addresses=list(exclude_addresses),detector_selects_research_samples=False,
                normalization="token1: p; token2: 1-p; reverse token2 taker action; cash unchanged",
                warnings=["Recorded-fill notional is not audited economic turnover.",
                    "Wallets are not verified humans; exchange filtering does not remove bots.",
                    "Market metadata is a snapshot, not information known at historical decision time."])
            con.execute("CREATE TABLE provenance (document VARCHAR)")
            con.execute("INSERT INTO provenance VALUES (?)", [json.dumps(audit)])
            for table in ("stage", "unique_fills", "metadata", "summaries"):
                con.execute(f"DROP TABLE {table}")
            con.execute("CHECKPOINT")
            con.close()
            os.replace(temp, db_path)
            return audit
        except BaseException:
            con.close()
            temp.unlink(missing_ok=True)
            Path(str(temp)+".wal").unlink(missing_ok=True)
            raise


def aggregate_sql(source: str, seconds: int) -> str:
    if seconds not in LEVELS.values() or not seconds:
        raise ValueError("Invalid aggregation interval")
    return f"""WITH bucketed AS (
        SELECT *, (timestamp // {seconds}) * {seconds} AS bin_start FROM ({source})
    ), weighted AS (
        SELECT *, sum(token_amount) OVER (PARTITION BY bin_start ORDER BY yes_price
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_weight,
            sum(token_amount) OVER (PARTITION BY bin_start) AS total_weight FROM bucketed
    ) SELECT bin_start,bin_start+{seconds} AS bin_end,
        first(yes_price ORDER BY {ORDER}) AS open,max(yes_price) AS high,min(yes_price) AS low,
        last(yes_price ORDER BY {ORDER}) AS close,
        sum(yes_price*token_amount)/sum(token_amount) AS vwap,
        min(yes_price) FILTER(WHERE cumulative_weight>=total_weight/2) AS weighted_median,
        sum(usd_amount) AS notional,sum(token_amount) AS token_volume,
        count(*) AS fill_count,count(DISTINCT transaction_hash) AS tx_count,
        min(timestamp) AS first_trade_ts,max(timestamp) AS last_trade_ts
        FROM weighted GROUP BY bin_start ORDER BY bin_start"""


class Store:
    def __init__(self, db: str | Path, cache: str | Path = "cache"):
        self.db, self.cache = Path(db).resolve(), Path(cache).resolve()

    def connect(self):
        if not self.db.exists():
            raise FileNotFoundError("No archive connected. Run xvi ingest, or start with --demo.")
        return duckdb().connect(str(self.db), read_only=True, config={"threads": 4})

    def audit(self) -> dict:
        with self.connect() as con:
            audit = json.loads(con.execute("SELECT document FROM provenance").fetchone()[0])
        if audit["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported archive schema; re-ingest with this version.")
        return audit

    def market(self, market_id: str) -> dict:
        identifier(market_id)
        with self.connect() as con:
            result = rows(con,"SELECT * FROM markets WHERE market_id=?",[market_id])
        if not result:
            raise KeyError("Market not in this archive. Ingest its original fills first.")
        m = result[0]
        m["metadata_outcome"] = metadata_outcome(m["closed"],m["outcome_prices"],m["answer1"],m["answer2"])
        m["resolution_ts"] = epoch(m["resolution_timestamp"])
        return m

    def directory(self, market_id: str) -> Path:
        return self.cache / f"v{SCHEMA_VERSION}" / self.audit()["snapshot"] / identifier(market_id)

    @staticmethod
    def _copy(con, query: str, destination: Path):
        temp = destination.with_name(destination.name+".tmp-"+uuid.uuid4().hex)
        try:
            con.execute(f"COPY ({query}) TO {literal(temp)} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 16384)")
            os.replace(temp,destination)
        finally:
            temp.unlink(missing_ok=True)

    def ensure_level(self, market_id: str, level: str) -> Path:
        if level not in LEVELS:
            raise ValueError("Unknown resolution")
        if not self.market(market_id)["fill_count"]:
            raise ValueError("Metadata only: no archived fills for this sibling.")
        directory = self.directory(market_id)
        directory.mkdir(parents=True,exist_ok=True)
        destination = directory / f"{level}.parquet"
        with FileLock(str(directory/"build.lock"),timeout=1200):
            if destination.exists():
                return destination
            with self.connect() as con:
                raw = directory/"raw.parquet"
                if not raw.exists():
                    self._copy(con,f"SELECT * FROM trades WHERE market_id={literal(market_id)} ORDER BY {ORDER}",raw)
                if level != "raw":
                    self._copy(con,aggregate_sql(f"SELECT * FROM read_parquet({literal(raw)})",LEVELS[level]),destination)
        return destination

    def series(self, market_id: str, start: int | None = None, end: int | None = None,
               level: str = "auto", target: int = 1400) -> dict:
        market = self.market(market_id)
        if not market["fill_count"]:
            return {"market":market,"rows":[],"level":"raw","empty":True}
        start = market["first_ts"] if start is None else int(start)
        end = market["last_ts"]+1 if end is None else int(end)
        if start<0 or end<=start or end>4102444801 or not 100<=target<=3000:
            raise ValueError("Use a valid [start,end) Unix-second window and target between 100 and 3000.")
        if level not in {"auto",*LEVELS}:
            raise ValueError("Unknown resolution")
        raw = self.ensure_level(market_id,"raw")
        source = f"SELECT * FROM read_parquet({literal(raw)}) WHERE timestamp>={start} AND timestamp<{end}"
        with self.connect() as con:
            count = con.execute(f"SELECT count(*) FROM ({source})").fetchone()[0]
            selected = choose_level(count,end-start,target) if level=="auto" else level
            previous = rows(con,f"SELECT * FROM read_parquet({literal(raw)}) WHERE timestamp<{start} ORDER BY {DESC} LIMIT 1")
        if selected=="raw":
            if count>6000:
                raise OverflowError(f"{count:,} raw fills. Zoom in or select Auto; no fills were silently dropped.")
            with self.connect() as con:
                data = rows(con,source+f" ORDER BY {ORDER}")
        else:
            seconds = LEVELS[selected]
            path = self.ensure_level(market_id,selected)
            with self.connect() as con:
                data = rows(con,f"SELECT * FROM read_parquet({literal(path)}) WHERE bin_start>={start} AND bin_end<={end} ORDER BY bin_start LIMIT 6001")
                if len(data)>6000:
                    raise OverflowError("More than 6,000 observed bins. Choose Auto or a coarser resolution.")
                # Recompute clipped edges from raw, never include an out-of-window fill.
                boundaries = set()
                if start % seconds:
                    boundaries.add(start//seconds*seconds)
                if end % seconds:
                    boundaries.add(end//seconds*seconds)
                for b in boundaries:
                    lo,hi = max(start,b),min(end,b+seconds)
                    if lo<hi:
                        query = f"SELECT * FROM read_parquet({literal(raw)}) WHERE timestamp>={lo} AND timestamp<{hi}"
                        partial = rows(con,aggregate_sql(query,seconds))
                        for row in partial:
                            row["partial"] = True
                        data.extend(partial)
                data.sort(key=lambda r:r["bin_start"])
                if len(data)>6000:
                    raise OverflowError("More than 6,000 observed bins. Narrow the window.")
        with self.connect() as con:
            stats = rows(con,f"""SELECT count(*) AS fill_count,count(DISTINCT transaction_hash) AS tx_count,
                coalesce(sum(usd_amount),0) AS notional,count(DISTINCT timestamp // 30) AS occupied_30s,
                min(yes_price) AS low,max(yes_price) AS high,
                first(yes_price ORDER BY {ORDER}) AS first_price,last(yes_price ORDER BY {ORDER}) AS last_price,
                max(timestamp) AS last_ts FROM ({source})""")[0]
        return dict(market=market,rows=data,level=selected,bin_seconds=LEVELS[selected],start=start,end=end,
                    stats=stats,previous_observation=previous[0] if previous else None,
                    forward_filled=False,detector_selects_research_samples=False)

    def event(self, event_id: str, as_of: int | None = None, stale_after: int = 300) -> dict:
        identifier(event_id)
        with self.connect() as con:
            siblings = rows(con,"SELECT * FROM markets WHERE event_id=? ORDER BY notional DESC,market_id",[event_id])
            if not siblings:
                raise KeyError("Event not present in this archive.")
            at = as_of if as_of is not None else max(m["last_ts"] or 0 for m in siblings)
            for market in siblings:
                latest = rows(con,f"SELECT yes_price,timestamp FROM trades WHERE market_id=? AND timestamp<=? ORDER BY {DESC} LIMIT 1",[market["market_id"],at])
                market["as_of_price"] = latest[0]["yes_price"] if latest else None
                market["age_seconds"] = at-latest[0]["timestamp"] if latest else None
                market["stale"] = not latest or market["age_seconds"]>stale_after
        complete = all(m["as_of_price"] is not None for m in siblings)
        return dict(event_id=event_id,title=siblings[0]["event_title"],as_of=at,siblings=siblings,
                    sum=sum(m["as_of_price"] for m in siblings) if complete else None,
                    stale_siblings=sum(m["stale"] for m in siblings),
                    all_archived_siblings_observed=complete,universe_verified_exhaustive=False,
                    note="Asynchronous last fills of archived siblings; not executable quotes or a no-arbitrage test.")
