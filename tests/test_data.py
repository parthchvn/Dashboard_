import csv
import json
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from filelock import Timeout
from xvi.data import Store,ingest,EXCHANGES,literal
from xvi.app import create_app

T=1710000000


def fill(i,**overrides):
    record=dict(market_id='1',event_id='event-1',timestamp=T+i,block_number=60000000+i,
        log_index=i,transaction_hash=f'0x{i:064x}',contract=EXCHANGES[0],maker='0xmaker',
        taker='0xtaker',price=.6,token_amount=2,usd_amount=1.2,nonusdc_side='token1',taker_direction='BUY')
    record.update(overrides)
    return record


def write(path,records):
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


@pytest.fixture
def archive(tmp_path):
    records=[fill(0),fill(10,transaction_hash=fill(0)['transaction_hash'],price=.2,
        nonusdc_side='token2',token_amount=3,usd_amount=.6),
        fill(30,price=.5,token_amount=4,usd_amount=2,taker_direction='SELL'),
        fill(120,price=.6,nonusdc_side='token2',token_amount=8,usd_amount=4.8,taker_direction='SELL'),
        fill(11,market_id='2',price=.15,token_amount=10,usd_amount=1.5),
        fill(20,taker=EXCHANGES[1],price=.9),fill(0),fill(15,price=1.4)]
    path=tmp_path/'trades.csv'
    write(path,records)
    metadata=tmp_path/'markets.csv'
    write(metadata,[dict(id=str(i),question=f'Will candidate {i} win?',event_id='event-1',
        event_title='Example event',answer1='Yes',answer2='No',closed='0',outcome_prices='[]') for i in (1,2,3)])
    db=tmp_path/'archive.duckdb'
    audit=ingest([str(path)],db,str(metadata),revision='test-revision',memory='512MB',threads=2)
    return Store(db,tmp_path/'cache'),audit,path,metadata


def test_audit_and_normalization(archive):
    store,audit,_,_=archive
    assert audit['input_rows']==8
    assert audit['invalid_rows_removed']==1
    assert audit['duplicate_logs_removed']==1
    assert audit['exchange_rows_excluded']==1
    assert audit['displayed_fills']==5
    assert audit['first_ts']==T and audit['last_ts']==T+120
    d=store.series('1',level='raw')['rows']
    assert len(d)==4
    assert d[1]['yes_price']==pytest.approx(.8)
    assert d[1]['raw_price']==pytest.approx(.2)
    assert d[1]['yes_direction']=='SELL'
    assert d[1]['usd_amount']==pytest.approx(.6)
    assert d[3]['yes_direction']=='BUY'
    with store.connect() as con:
        assert con.execute('SELECT count(*) FROM fills').fetchone()[0]==6


def test_exact_bins_cash_and_lower_weighted_median(archive):
    store,*_=archive
    data=store.series('1',start=T,end=T+150,level='30s')
    assert [b['bin_end'] for b in data['rows']]==[T+30,T+60,T+150]
    first=data['rows'][0]
    assert first['fill_count']==2 and first['tx_count']==1
    assert first['open']==pytest.approx(.6) and first['close']==pytest.approx(.8)
    assert first['weighted_median']==pytest.approx(.8)
    assert first['vwap']==pytest.approx(.72)
    assert first['notional']==pytest.approx(1.8)
    assert not data['forward_filled']
    assert data['stats']['occupied_30s']==3


def test_boundary_bars_have_no_out_of_window_fills(archive):
    store,*_=archive
    data=store.series('1',start=T+5,end=T+25,level='30s')
    assert len(data['rows'])==1
    row=data['rows'][0]
    assert row['fill_count']==1 and row['vwap']==pytest.approx(.8)
    assert row['partial'] and row['bin_end']==T+30
    exact=store.series('1',start=T+30,end=T+60,level='30s')['rows'][0]
    assert exact['fill_count']==1 and exact['close']==.5


def test_gaps_are_empty_and_previous_price_is_separate(archive):
    store,*_=archive
    data=store.series('1',start=T+61,end=T+100,level='30s')
    assert data['rows']==[]
    assert data['stats']['fill_count']==0 and data['stats']['last_price'] is None
    assert data['previous_observation']['timestamp']==T+30


def test_all_levels_recompute_distinct_transactions(archive):
    store,*_=archive
    for level in ['raw','30s','1m','5m','1h','1d']:
        path=store.ensure_level('1',level)
        assert path.exists()
        with store.connect() as con:
            assert con.execute('SELECT count(*) FROM read_parquet(?)',[str(path)]).fetchone()[0]>0
    bar=store.series('1',start=T,end=T+300,level='5m')['rows'][0]
    assert bar['tx_count']==3 and bar['fill_count']==4
    assert bar['weighted_median']==pytest.approx(.5)


def test_event_asof_is_not_future_or_missing_sibling_filled(archive):
    store,*_=archive
    event=store.event('event-1',as_of=T+15,stale_after=5)
    assert len(event['siblings'])==3 and event['sum'] is None
    m=next(m for m in event['siblings'] if m['market_id']=='1')
    assert m['as_of_price']==pytest.approx(.8) and m['age_seconds']==5 and not m['stale']
    assert not event['universe_verified_exhaustive']


def test_conflicts_do_not_replace_a_good_snapshot(archive):
    store,audit,path,metadata=archive
    bad=path.with_name('bad.csv')
    write(bad,[fill(1),fill(1,price=.7)])
    with pytest.raises(ValueError,match='Conflicting'):
        ingest([str(bad)],store.db,str(metadata),replace=True,memory='512MB')
    assert store.audit()['snapshot']==audit['snapshot']


def test_replacement_is_explicit_and_invalidates_cache(archive):
    store,audit,path,metadata=archive
    with pytest.raises(FileExistsError):
        ingest([str(path)],store.db,str(metadata))
    old=store.ensure_level('1','30s')
    new=ingest([str(path)],store.db,str(metadata),replace=True,memory='512MB')
    assert new['snapshot']!=audit['snapshot']
    assert old!=store.ensure_level('1','30s') and old.exists()


@pytest.mark.parametrize('column',['yes_price','price_outcome1','user','role'])
def test_already_normalized_rows_are_rejected(tmp_path,column):
    path=tmp_path/'input.csv'
    write(path,[{**fill(1),column:'bad'}])
    with pytest.raises(ValueError,match='already-normalized'):
        ingest([str(path)],tmp_path/'bad.duckdb',memory='512MB')


def test_native_parquet_and_quoted_paths(archive,tmp_path):
    store,_,path,metadata=archive
    folder=tmp_path/"quote's directory"
    folder.mkdir()
    parquet=folder/'trades.parquet'
    with duckdb.connect() as con:
        con.execute(f'COPY (SELECT * FROM read_csv({literal(path)},all_varchar=true)) TO {literal(parquet)} (FORMAT PARQUET)')
    result=ingest([str(parquet)],folder/'native.duckdb',str(metadata),memory='512MB')
    assert result['displayed_fills']==5


def test_chain_order_with_second_precision(tmp_path):
    path=tmp_path/'tie.csv'
    write(path,[fill(1,timestamp=T,block_number=10,log_index=4,price=.9),
                fill(2,timestamp=T,block_number=10,log_index=2,price=.3),
                fill(3,timestamp=T,block_number=9,log_index=8,price=.1),
                fill(4,timestamp=T+.5)])
    db=tmp_path/'tie.duckdb'
    audit=ingest([str(path)],db,memory='512MB')
    assert audit['invalid_rows_removed']==1
    store=Store(db,tmp_path/'cache')
    assert [r['yes_price'] for r in store.series('1',level='raw')['rows']]==[.1,.3,.9]
    row=store.series('1',start=T,end=T+30,level='30s')['rows'][0]
    assert row['open']==.1 and row['close']==.9


def test_api_exports_search_security_and_limits(archive):
    store,*_=archive
    with TestClient(create_app(str(store.db),str(store.cache))) as client:
        assert client.get('/').status_code==200
        assert "script-src 'self'" in client.get('/').headers['content-security-policy']
        assert client.get('/api/status').json()['ready']
        assert len(client.get('/api/markets?q=1').json())==1
        assert client.get('/api/markets',params={'q':"%' OR 1=1 --"}).json()==[]
        assert client.get('/api/markets/unknown/series').status_code==404
        assert client.get('/api/markets/1/series?start=10&end=9').status_code==400
        assert client.get('/api/markets/1/series?level=garbage').status_code==400
        assert client.get('/api/markets/1/series?target=99999').status_code==422
        assert client.get('/api/markets/1/reviews').json()['annotations']==[]
        data=list(csv.DictReader(client.get('/api/markets/1/export.csv').text.splitlines()))
        assert len(data)==4 and data[1]['yes_direction']=='SELL'
        first=client.get('/api/markets/1/trades?limit=2').json()
        second=client.get('/api/markets/1/trades?limit=2&offset=2').json()
        assert first['has_more'] and not second['has_more']
        assert first['rows'][-1]['timestamp']>second['rows'][0]['timestamp']


def test_serving_snapshot_is_locked_against_replacement(archive):
    store,_,path,metadata=archive
    with TestClient(create_app(str(store.db),str(store.cache))):
        with pytest.raises(Timeout):
            ingest([str(path)],store.db,str(metadata),replace=True)


def test_raw_limit_is_explicit_auto_preserves_fill_counts(tmp_path):
    p=tmp_path/'many.csv'
    write(p,[fill(i) for i in range(6001)])
    db=tmp_path/'many.duckdb'
    ingest([str(p)],db,memory='512MB')
    store=Store(db,tmp_path/'cache')
    with pytest.raises(OverflowError,match='silently'):
        store.series('1',level='raw')
    auto=store.series('1',target=100)
    assert auto['level']!='raw' and len(auto['rows'])<=102
    assert sum(r['fill_count'] for r in auto['rows'])==6001


def test_missing_database_never_silently_uses_demo(tmp_path):
    client=TestClient(create_app(str(tmp_path/'missing.duckdb'),str(tmp_path/'cache')))
    assert client.get('/api/status').json()['ready'] is False
    assert client.get('/api/markets').status_code==503


def test_review_adapter_preserves_full_history_and_review_only(archive,tmp_path):
    from xvi.review import build_reviews
    store,*_=archive
    directory=tmp_path/'external'/'polymarket_context'
    directory.mkdir(parents=True)
    (directory/'fine.py').write_text('''class FineConfig:
    fingerprint = 'stub-config'
    def __init__(self, **kwargs): self.kwargs = kwargs

def make_fine_bars(frame, cfg):
    assert len(frame) == 4
    assert str(frame.timestamp.dtype) == 'datetime64[ns, UTC]'
    assert abs(frame.price_outcome1.iloc[1] - 0.8) < 1e-12
    return frame

def detect_fine(frame, cfg):
    return None, [{'detected_at': frame.timestamp.iloc[-1].isoformat(), 'median_price': 0.4}]
''')
    result=build_reviews(store,'1',str(directory.parent),'30s')
    assert result['annotations']==1
    doc=json.loads((store.directory('1')/'reviews-30s.json').read_text())
    assert not doc['detector_selects_research_samples']
    assert doc['annotations'][0]['timestamp']==T+120
