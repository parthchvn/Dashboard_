"""Deterministic fictional fixtures. Never historical Polymarket observations."""
from __future__ import annotations
import csv
import math
from pathlib import Path
import random
from .domain import EXCHANGES


def generate(directory: str | Path, days: int = 90) -> tuple[Path,Path]:
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    rng = random.Random(16)
    start = 1740787200  # A fixed synthetic historical fixture, not live data.
    names = ["Aurora","Meridian","Helios"]
    fields = ["market_id","event_id","timestamp","block_number","transaction_hash","log_index",
              "contract","maker","taker","price","usd_amount","token_amount","nonusdc_side","taker_direction"]
    trades,metadata = directory/"trades.csv",directory/"markets.csv"
    with trades.open("w",newline="") as file:
        writer = csv.DictWriter(file,fieldnames=fields)
        writer.writeheader()
        n = 0
        for step in range(days*144):
            x = step/(days*144)
            if .26<x<.28 or .68<x<.705 or rng.random()<.22:
                continue
            a = .28+.11*math.sin(x*15)+.12*x+.32/(1+math.exp(-40*(x-.72)))
            a = min(.985,a+.35/(1+math.exp(-90*(x-.95))))
            base = [a,(1-a)*.64,(1-a)*.36]
            for j,name in enumerate(names):
                if j and rng.random()<.25:
                    continue
                n += 1
                t = start+step*600+rng.randrange(0,100)
                yes = max(.003,min(.997,base[j]+rng.uniform(-.008,.008)))
                token = "token2" if rng.random()<.35 else "token1"
                price = yes if token=="token1" else 1-yes
                shares = round(rng.lognormvariate(4.5,1.2),3)
                writer.writerow(dict(market_id=f"demo-{name.lower()}",event_id="demo-championship",
                    timestamp=t,block_number=60000000+(t-start)//2,transaction_hash="0x"+f"{n:064x}",
                    log_index=n%12,contract=EXCHANGES[0],maker="0x"+f"{n%79+100:040x}",
                    taker="0x"+f"{n%127+300:040x}",price=round(price,6),
                    usd_amount=round(price*shares,6),token_amount=shares,nonusdc_side=token,
                    taker_direction=rng.choice(["BUY","SELL"])))
    with metadata.open("w",newline="") as file:
        writer = csv.DictWriter(file,fieldnames=["id","question","event_id","event_title","answer1","answer2","closed","outcome_prices"])
        writer.writeheader()
        for name in names:
            writer.writerow(dict(id=f"demo-{name.lower()}",question=f"Will {name} win the championship?",
                event_id="demo-championship",event_title="Demo championship winner",
                answer1="Yes",answer2="No",closed="0",outcome_prices="[]"))
    return trades,metadata
