"""Optional adapter to the EXISTING news_attr fine.py; no new spike detector.

This imports trusted local Python code and runs on full observed market history.
Its output is a review overlay, never research samples or inferred explanations.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid
from .data import Store, ORDER
from .domain import LEVELS, clean_json, epoch


def build_reviews(store: Store, market_id: str, checkout: str, level: str = "30s") -> dict:
    import pandas as pd
    if level not in LEVELS or level=="raw":
        raise ValueError("Reviews require an observed-bin level.")
    path = Path(checkout).resolve()/"polymarket_context"/"fine.py"
    if not path.is_file():
        raise FileNotFoundError("Expected a trusted checkout containing polymarket_context/fine.py")
    spec = importlib.util.spec_from_file_location("xvi_external_fine",path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    seconds = LEVELS[level]
    cfg = module.FineConfig(bin_seconds=seconds,horizons_seconds=tuple(seconds*x for x in (1,2,6,10,30)))
    with store.connect() as con:
        frame = con.execute(f"""SELECT market_id,timestamp,yes_price AS price_outcome1,
            token_amount,usd_amount,transaction_hash FROM trades WHERE market_id=? ORDER BY {ORDER}""",[market_id]).fetchdf()
    if frame.empty:
        raise ValueError("No observed fills for this market.")
    frame["timestamp"] = pd.to_datetime(frame.timestamp,unit="s",utc=True)
    bars = module.make_fine_bars(frame,cfg)
    _,alerts = module.detect_fine(bars,cfg)
    annotations = []
    for alert in alerts:
        at = epoch(alert.get("detected_at"))
        if at is None:
            raise ValueError("The existing detector returned an alert without detected_at.")
        annotations.append({"timestamp":at,"price":alert.get("median_price",alert.get("price")),"record":clean_json(alert)})
    doc = dict(available=True,snapshot=store.audit()["snapshot"],level=level,
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),config_fingerprint=cfg.fingerprint,
        annotations=annotations,detector_selects_research_samples=False)
    directory = store.directory(market_id)
    directory.mkdir(parents=True,exist_ok=True)
    destination = directory/f"reviews-{level}.json"
    temp = destination.with_name(destination.name+".tmp-"+uuid.uuid4().hex)
    try:
        temp.write_text(json.dumps(doc,allow_nan=False),encoding="utf-8")
        os.replace(temp,destination)
    finally:
        temp.unlink(missing_ok=True)
    return {"annotations":len(annotations),"level":level,"source_sha256":doc["source_sha256"]}
