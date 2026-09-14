from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path
from filelock import Timeout
from .data import Store, ingest
from .domain import LEVELS


def main() -> None:
    parser = argparse.ArgumentParser(description="XVI Market Observatory — historical fills, not quotes")
    commands = parser.add_subparsers(dest="command",required=True)
    load = commands.add_parser("ingest",help="Ingest a local original-trades snapshot")
    load.add_argument("--trades",nargs="+",required=True)
    load.add_argument("--markets")
    load.add_argument("--db",default="data/polymarket.duckdb")
    load.add_argument("--source-revision",default="unspecified")
    load.add_argument("--memory",default="4GB")
    load.add_argument("--threads",type=int,default=4)
    load.add_argument("--replace",action="store_true")
    build = commands.add_parser("build",help="Warm a market cache; otherwise built lazily")
    build.add_argument("market_id")
    build.add_argument("--db",default="data/polymarket.duckdb")
    build.add_argument("--cache",default="cache")
    build.add_argument("--level",choices=["all",*LEVELS],default="all")
    review = commands.add_parser("review",help="Run existing news_attr diagnostics as a review overlay")
    review.add_argument("market_id")
    review.add_argument("--news-attr",required=True)
    review.add_argument("--level",choices=list(LEVELS)[1:],default="30s")
    review.add_argument("--db",default="data/polymarket.duckdb")
    review.add_argument("--cache",default="cache")
    serve = commands.add_parser("serve",help="Start the local dashboard")
    group = serve.add_mutually_exclusive_group()
    group.add_argument("--db")
    group.add_argument("--demo",action="store_true")
    serve.add_argument("--cache",default="cache")
    serve.add_argument("--host",default="127.0.0.1")
    serve.add_argument("--port",type=int,default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command=="ingest":
            result = ingest(args.trades,args.db,args.markets,replace=args.replace,
                memory=args.memory,threads=args.threads,revision=args.source_revision)
            print(json.dumps(result,indent=2))
        elif args.command=="build":
            store = Store(args.db,args.cache)
            for level in LEVELS if args.level=="all" else [args.level]:
                print(store.ensure_level(args.market_id,level))
        elif args.command=="review":
            from .review import build_reviews
            print(json.dumps(build_reviews(Store(args.db,args.cache),args.market_id,args.news_attr,args.level),indent=2))
        else:
            import uvicorn
            from .app import create_app
            db = args.db or "data/polymarket.duckdb"
            if args.demo:
                from .demo import generate
                db = "data/demo/demo.duckdb"
                if not Path(db).exists():
                    trades,metadata = generate("data/demo")
                    ingest([str(trades)],db,str(metadata),revision="synthetic-seed-16",demo=True)
            if args.host not in {"127.0.0.1","localhost","::1"}:
                logging.warning("No built-in authentication. Use an authenticated reverse proxy before exposing this server.")
            uvicorn.run(create_app(db,args.cache),host=args.host,port=args.port,workers=1)
    except Timeout:
        parser.exit(2,"Error: snapshot/cache is locked. Stop the server before re-ingesting.\n")
    except (ValueError,FileNotFoundError,FileExistsError,ImportError) as exc:
        parser.exit(2,f"Error: {exc}\n")


if __name__=="__main__":
    main()
