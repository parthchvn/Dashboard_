"""Manual granularity is exact, market-scoped, bounded, and never downsampled."""
import csv

import pytest
from fastapi.testclient import TestClient
from xvi.app import create_app
from xvi.data import Store, ingest
from xvi.domain import EXCHANGES, LEVELS, level_seconds, normalize_level

A, B = '0x'+'a'*40, '0x'+'b'*40
T = 1740787200


@pytest.mark.parametrize('value,canonical,seconds', [
    ('raw','raw',0), ('30s','30s',30), ('1m','1m',60), ('2m','2m',120),
    ('3m','3m',180), ('7m','7m',420), ('60m','1h',3600),
    ('240m','4h',14400), ('1440m','1d',86400), ('1439m','1439m',86340),
])
def test_interval_validation_and_canonicalization(value,canonical,seconds):
    assert normalize_level(value)==canonical
    assert level_seconds(value)==seconds


@pytest.mark.parametrize('value', ['0m','-1m','1.5m','1441m','01m','1e2m','2m;DROP TABLE trades',
                                  '../raw','', '2minutes', '2s', '2h', None, 2])
def test_invalid_intervals_fail_closed(value):
    with pytest.raises(ValueError,match='resolution'):
        normalize_level(value)


def test_auto_is_explicit_and_presets_are_ordered():
    assert normalize_level('auto',allow_auto=True)=='auto'
    with pytest.raises(ValueError):
        level_seconds('auto')
    assert list(LEVELS.values())==sorted(LEVELS.values())


@pytest.fixture
def store(tmp_path):
    # Unequal weights, two logs in the same transaction in different 1m bins,
    # wallet roles, another market, a boundary at 120s, and a long empty gap.
    records=[]
    for i,(ts,p,w,maker,taker) in enumerate([
        (0,.2,1,A,B), (59,.8,4,B,A), (60,.4,3,A,B),
        (119,.6,2,B,A), (120,.3,8,A,B), (599,.9,1,B,A),
    ]):
        records.append(dict(market_id='1',timestamp=T+ts,block_number=60000000+i,
            log_index=i,transaction_hash=f'0x{(1 if i in (1,2) else i):064x}',contract=EXCHANGES[0],
            maker=maker,taker=taker,price=p,token_amount=w,usd_amount=p*w,
            nonusdc_side='token1',taker_direction='BUY'))
    records.append({**records[0],'market_id':'2','log_index':100,'price':.99})
    path=tmp_path/'trades.csv'
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=records[0]);writer.writeheader();writer.writerows(records)
    db=tmp_path/'archive.duckdb'
    ingest([str(path)],db,memory='512MB',threads=2)
    return Store(db,tmp_path/'cache')


def test_two_minute_bars_are_not_averaged_or_decimated_one_minute_bars(store):
    one=store.series('1',T,T+600,'1m')
    two=store.series('1',T,T+600,'2m')
    assert two['level']=='2m' and two['bin_seconds']==120
    assert [r['bin_start'] for r in two['rows']]==[T,T+120,T+480]
    b=two['rows'][0]
    assert (b['open'],b['high'],b['low'],b['close'])==(.2,.8,.2,.6)
    assert b['vwap']==pytest.approx(.58) and b['weighted_median']==.6
    assert b['fill_count']==4 and b['tx_count']==3
    assert sum(r['tx_count'] for r in one['rows'][:2])==4  # naive sum is wrong
    assert b['notional']==pytest.approx(5.8) and b['token_volume']==10
    assert one['stats']==two['stats']
    assert not two['forward_filled']
    assert store.ensure_level('1','1m')!=store.ensure_level('1','2m')


@pytest.mark.parametrize('level',['2m','3m','7m','15m','30m','4h','1439m'])
@pytest.mark.parametrize('wallets,role',[('', 'either'),(A,'either'),(A,'maker'),(A,'taker')])
def test_every_interval_uses_exact_window_and_wallet_subset(store,level,wallets,role):
    start,end=T+50,T+590
    raw=store.series('1',start,end,'raw',wallets=wallets,wallet_role=role)
    data=store.series('1',start,end,level,wallets=wallets,wallet_role=role)
    seconds=level_seconds(level)
    expected={}
    for row in raw['rows']:
        expected.setdefault(row['timestamp']//seconds*seconds,[]).append(row)
    assert [r['bin_start'] for r in data['rows']]==list(expected)
    assert data['stats']==raw['stats']
    for bar in data['rows']:
        fills=expected[bar['bin_start']]
        assert bar['bin_end']-bar['bin_start']==seconds
        assert bar['fill_count']==len(fills)
        assert bar['tx_count']==len({r['transaction_hash'] for r in fills})
        assert bar['open']==fills[0]['yes_price'] and bar['close']==fills[-1]['yes_price']
        assert bar['notional']==pytest.approx(sum(r['usd_amount'] for r in fills))
        shares=sum(r['token_amount'] for r in fills)
        assert bar['vwap']==pytest.approx(sum(r['yes_price']*r['token_amount'] for r in fills)/shares)
        weight=0
        for row in sorted(fills,key=lambda r:r['yes_price']):
            weight+=row['token_amount']
            if weight>=shares/2:
                assert bar['weighted_median']==row['yes_price']
                break
        assert bool(bar.get('partial'))==(bar['bin_start']<start or bar['bin_end']>end)
    assert data['previous_observation']==raw['previous_observation']
    assert not data['forward_filled'] and not data['detector_selects_research_samples']


def test_custom_does_not_create_full_archive_cache_and_alias_reuses_preset(store):
    data=store.series('1',T,T+600,'7m')
    assert data['bin_seconds']==420
    assert not (store.directory('1')/'7m.parquet').exists()
    assert store.series('1',T,T+600,'60m')==store.series('1',T,T+600,'1h')


def test_api_custom_wallet_bars_csv_empty_states_and_invalid_intervals(store):
    with TestClient(create_app(str(store.db),str(store.cache))) as client:
        params=dict(start=T,end=T+600,wallets=A,wallet_role='taker')
        for level in ['1m','2m','7m','60m']:
            response=client.get('/api/markets/1/series',params={**params,'level':level})
            assert response.status_code==200
            data=response.json()
            assert data['stats']['fill_count']==3
            assert data['bin_seconds']==level_seconds(level)
        tape=client.get('/api/markets/1/trades',params=params).json()['rows']
        exported=list(csv.DictReader(client.get('/api/markets/1/export.csv',params=params).text.splitlines()))
        assert len(exported)==len(tape)==3
        review=client.get('/api/markets/1/reviews',params={'level':'7m'})
        assert review.status_code==200 and not review.json()['available']
        for bad in ['0m','1441m','1.5m','../../bad']:
            assert client.get('/api/markets/1/series',params={'level':bad}).status_code==400
            assert client.get('/api/markets/1/reviews',params={'level':bad}).status_code==400
        empty=client.get('/api/markets/1/series',params={**params,'level':'7m','wallets':'0x'+'f'*40}).json()
        assert empty['rows']==[] and empty['stats']['fill_count']==0 and empty['level']=='7m'
