"""Local read-only API. No remote downloads, trading, or arbitrary SQL endpoints."""
from __future__ import annotations

from contextlib import asynccontextmanager
import csv
import io
import json
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout
from .data import Store, ORDER, DESC, rows
from .domain import LEVELS, clean_json, identifier, parse_wallets, wallet_predicate


def create_app(db: str = "data/polymarket.duckdb", cache: str = "cache") -> FastAPI:
    store = Store(db,cache)

    @asynccontextmanager
    async def lifespan(app):
        # Protect a serving snapshot against CLI replacement, even on Unix.
        lock = FileLock(str(store.db)+".snapshot.lock",timeout=1) if store.db.exists() else None
        if lock:
            lock.acquire()
        try:
            yield
        finally:
            if lock:
                lock.release()

    app = FastAPI(title="XVI Market Observatory",version="0.1.0",lifespan=lifespan)
    app.state.store = store
    static = Path(__file__).parent/"static"
    app.mount("/static",StaticFiles(directory=static),name="static")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def handler(status):
        async def handle(request: Request, exc: Exception):
            return JSONResponse({"detail":str(exc).strip("'")},status_code=status)
        return handle
    for error,status in ((ValueError,400),(KeyError,404),(FileNotFoundError,503),(OverflowError,413),(Timeout,503)):
        app.add_exception_handler(error,handler(status))

    @app.get("/")
    def index():
        return FileResponse(static/"index.html")

    @app.get("/api/status")
    def status():
        if not store.db.exists():
            return {"ready":False,"message":"No archive connected. Ingest original trades or start with --demo."}
        return {"ready":True,**store.audit()}

    @app.get("/api/markets")
    def markets(q: str = Query("",max_length=200),limit: int = Query(80,ge=1,le=200),
                offset: int = Query(0,ge=0,le=1000000)):
        q = q.strip()
        with store.connect() as con:
            return rows(con,"""SELECT market_id,question,event_id,event_title,answer1,answer2,
                first_ts,last_ts,fill_count,notional,last_price FROM markets
                WHERE fill_count>0 AND (?='' OR contains(lower(question),lower(?)) OR market_id=? OR event_id=?)
                ORDER BY (market_id=?) DESC,notional DESC,market_id LIMIT ? OFFSET ?""",[q,q,q,q,q,limit,offset])

    @app.get("/api/events")
    def events(q: str = Query("",max_length=200),limit: int = Query(80,ge=1,le=200)):
        with store.connect() as con:
            return rows(con,"""SELECT event_id,max(event_title) AS title,count(*) AS siblings,
                sum(fill_count) AS fill_count,sum(notional) AS notional FROM markets
                WHERE event_id IS NOT NULL AND event_id<>'' AND
                (?='' OR contains(lower(event_title),lower(?)) OR event_id=?)
                GROUP BY event_id HAVING sum(fill_count)>0 ORDER BY notional DESC,event_id LIMIT ?""",[q,q,q,limit])

    @app.get("/api/markets/{market_id}/series")
    def series(market_id: str,start: int | None = Query(None,ge=0),end: int | None = Query(None,ge=1),
               level: str = "auto",target: int = Query(1400,ge=100,le=3000),
               wallets: str = Query("",max_length=8192),wallet_role: str = Query("either",max_length=10)):
        return clean_json(store.series(market_id,start,end,level,target,wallets=wallets,wallet_role=wallet_role))

    @app.get("/api/events/{event_id}")
    def event(event_id: str,as_of: int | None = Query(None,ge=0,le=4102444800),
              stale_after: int = Query(300,ge=1,le=604800)):
        return clean_json(store.event(event_id,as_of,stale_after))

    @app.get("/api/markets/{market_id}/trades")
    def trades(market_id: str,start: int = Query(0,ge=0),end: int = Query(4102444801,ge=1,le=4102444801),
               limit: int = Query(50,ge=1,le=200),offset: int = Query(0,ge=0,le=100000),
               wallets: str = Query("",max_length=8192),wallet_role: str = Query("either",max_length=10)):
        identifier(market_id)
        if end<=start:
            raise ValueError("end must be after start")
        addresses = parse_wallets(wallets)
        predicate, params = wallet_predicate(addresses, wallet_role)
        with store.connect() as con:
            data = rows(con,f"""SELECT * FROM trades WHERE market_id=? AND timestamp>=? AND timestamp<?
                AND ({predicate}) ORDER BY {DESC} LIMIT ? OFFSET ?""",[market_id,start,end,*params,limit+1,offset])
        return dict(rows=data[:limit],has_more=len(data)>limit,offset=offset,
                    filters={"wallets":list(addresses),"wallet_role":wallet_role},
                    note="One chain-log fill per row. Outcome-1 equivalent taker action, not verified human decisions.")

    @app.get("/api/markets/{market_id}/reviews")
    def reviews(market_id: str,level: str = "30s",start: int = Query(0,ge=0),end: int = Query(4102444801,ge=1)):
        if level not in LEVELS or end<=start:
            raise ValueError("Invalid review level or window")
        path = store.directory(market_id)/f"reviews-{level}.json"
        if not path.exists():
            return {"available":False,"annotations":[],"detector_selects_research_samples":False}
        doc = json.loads(path.read_text())
        doc["annotations"] = [r for r in doc["annotations"] if start<=r["timestamp"]<end]
        return clean_json(doc)

    @app.get("/api/markets/{market_id}/export.csv")
    def export(market_id: str,start: int = Query(0,ge=0),end: int = Query(4102444801,ge=1,le=4102444801),
               wallets: str = Query("",max_length=8192),wallet_role: str = Query("either",max_length=10)):
        identifier(market_id)
        if end<=start:
            raise ValueError("end must be after start")
        addresses = parse_wallets(wallets)
        predicate, wallet_params = wallet_predicate(addresses, wallet_role)
        params = [market_id,start,end,*wallet_params]
        where = f"FROM trades WHERE market_id=? AND timestamp>=? AND timestamp<? AND ({predicate})"
        with store.connect() as con:
            count = con.execute("SELECT count(*) "+where,params).fetchone()[0]
        if count>100000:
            raise OverflowError("CSV export is capped at 100,000 fills. Narrow the window or query the local database.")

        def stream():
            with store.connect() as con:
                cursor = con.execute("SELECT * "+where+f" ORDER BY {ORDER}",params)
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow([r[0] for r in cursor.description])
                yield buf.getvalue()
                while batch := cursor.fetchmany(1000):
                    buf.seek(0)
                    buf.truncate(0)
                    for record in batch:
                        writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in record])
                    yield buf.getvalue()
        return StreamingResponse(stream(),media_type="text/csv",headers={
            "Content-Disposition":f'attachment; filename="{market_id}-observed-fills.csv"'})

    return app
