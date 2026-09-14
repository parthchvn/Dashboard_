"""Wallet subsets must agree across raw fills, bars, stats, tape and exports."""
import csv
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from xvi.app import create_app
from xvi.data import Store, ingest
from xvi.domain import EXCHANGES, parse_wallets, wallet_predicate

A, B, C = ('0x' + x * 40 for x in ('a', 'b', 'c'))
T = int(datetime(2024, 3, 10, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def store(tmp_path):
    def fill(i, seconds, maker, taker, price, shares=1, market='1', token='token1'):
        return dict(market_id=market, timestamp=T+seconds, block_number=60000000+i,
                    log_index=i, transaction_hash=f'0x{i:064x}', contract=EXCHANGES[0],
                    maker=maker, taker=taker, price=price, token_amount=shares,
                    usd_amount=price*shares, nonusdc_side=token, taker_direction='BUY')
    records = [fill(1, 0, A, B, .2), fill(2, 10, C, A.upper(), .8, 3),
               fill(3, 30, A, A, .5, 2), fill(4, 50, C, B, .9),
               fill(5, 86399, C, A, .7, 2, token='token2'),
               fill(6, 86400, A, C, .4, 3), fill(7, 86405, A, B, .7, market='2')]
    path=tmp_path/'trades.csv'
    with path.open('w', newline='') as f:
        writer=csv.DictWriter(f,fieldnames=records[0])
        writer.writeheader();writer.writerows(records)
    db=tmp_path/'archive.duckdb'
    ingest([str(path)],db,memory='512MB',threads=2)
    return Store(db,tmp_path/'cache')


def test_address_parsing_is_exact_case_insensitive_and_deduplicated():
    assert parse_wallets(f' {A.upper()}, {B};\n{A} ') == (A,B)
    assert parse_wallets('') == ()
    assert parse_wallets(None) == ()
    assert parse_wallets((B,A)) == (A,B)
    clause,params=wallet_predicate((A,B),'either')
    assert A not in clause and params==[A,B,A,B] and ' OR ' in clause
    assert wallet_predicate((), 'maker') == ('TRUE', [])


@pytest.mark.parametrize('value',['0x123', "0x' OR 1=1 --", 'wallet.eth', '0x'+'g'*40, A+',bad'])
def test_invalid_wallets_are_rejected(value):
    with pytest.raises(ValueError,match='full wallet'):
        parse_wallets(value)


def test_limits_and_invalid_roles():
    with pytest.raises(ValueError,match='50'):
        parse_wallets(','.join(f'0x{i:040x}' for i in range(51)))
    with pytest.raises(ValueError,match='long'):
        parse_wallets(' '*8193)
    with pytest.raises(ValueError,match='role'):
        wallet_predicate((), 'maker) OR TRUE--')


@pytest.mark.parametrize('role,expected',[('maker',[1,3]),('taker',[2,3,5]),('either',[1,2,3,5])])
def test_raw_scope_and_role(store,role,expected):
    data=store.series('1',T,T+86400,'raw',wallets=A.upper(),wallet_role=role)
    assert [r['log_index'] for r in data['rows']]==expected
    assert data['stats']['fill_count']==len(expected)
    assert data['scope']=='wallet_subset' and data['filters']['wallets']==[A]
    assert all(r['market_id']=='1' and T<=r['timestamp']<T+86400 for r in data['rows'])


def test_multiple_wallets_and_self_matches_count_a_fill_once(store):
    data=store.series('1',T,T+86400,'raw',wallets=f'{A},{B},{A}')
    assert [r['log_index'] for r in data['rows']]==[1,2,3,4,5]
    assert data['stats']['fill_count']==5


@pytest.mark.parametrize('level',['30s','1m','5m','1h','1d'])
def test_every_level_aggregates_the_subset_and_does_not_poison_market_cache(store,level):
    original=store.series('1',T,T+86400,level)
    data=store.series('1',T,T+86400,level,wallets=A)
    assert sum(r['fill_count'] for r in data['rows'])==4
    assert sum(r['notional'] for r in data['rows'])==pytest.approx(5)
    assert data['stats']['fill_count']==4 and data['stats']['notional']==pytest.approx(5)
    assert store.series('1',T,T+86400,level)==original
    assert not data['forward_filled'] and not data['detector_selects_research_samples']


def test_filtered_weighted_aggregation_and_clipped_edges(store):
    data=store.series('1',T,T+60,'30s',wallets=A)
    first=data['rows'][0]
    assert first['vwap']==pytest.approx(.65)
    assert first['weighted_median']==.8 and first['open']==.2 and first['close']==.8
    assert first['tx_count']==2
    edge=store.series('1',T+5,T+20,'30s',wallets=A)['rows'][0]
    assert edge['fill_count']==1 and edge['vwap']==pytest.approx(.8) and edge['partial']
    combined=store.series('1',T,T+60,'1m',wallets=A)['rows'][0]
    assert combined['weighted_median']==.5 and combined['vwap']==pytest.approx(.6)


def test_previous_observation_is_from_selected_wallet_role_not_global_market(store):
    data=store.series('1',T+86401,T+86410,'raw',wallets=A,wallet_role='taker')
    assert data['rows']==[] and data['stats']['fill_count']==0
    assert data['previous_observation']['timestamp']==T+86399
    absent=store.series('1',T,T+86400,wallets='0x'+'d'*40)
    assert absent['rows']==[] and absent['previous_observation'] is None
    assert absent['stats']['last_price'] is None


def test_api_pagination_and_csv_agree_with_filtered_series(store):
    with TestClient(create_app(str(store.db),str(store.cache))) as client:
        params=dict(start=T,end=T+86400,wallets=A.upper(),wallet_role='taker')
        base='/api/markets/1/'
        series=client.get(base+'series',params=params).json()
        first=client.get(base+'trades',params={**params,'limit':2}).json()
        second=client.get(base+'trades',params={**params,'limit':2,'offset':2}).json()
        assert first['has_more'] and not second['has_more']
        assert [r['log_index'] for r in first['rows']+second['rows']]==[5,3,2]
        exported=list(csv.DictReader(client.get(base+'export.csv',params=params).text.splitlines()))
        assert [int(r['log_index']) for r in exported]==[2,3,5]
        assert len(exported)==series['stats']['fill_count']==3
        assert sum(float(r['usd_amount']) for r in exported)==pytest.approx(series['stats']['notional'])
        assert first['filters']==series['filters']
        assert not series['detector_selects_research_samples']
        # End is exclusive: final day's last second included; next midnight excluded.
        assert series['stats']['last_ts']==T+86399
        for endpoint in ('series','trades','export.csv'):
            assert client.get(base+endpoint,params={'wallets':"' OR 1=1--"}).status_code==400
            assert client.get(base+endpoint,params={'wallet_role':'both'}).status_code==400
            assert client.get(base+endpoint,params={'wallets':A,'end':T,'start':T+1}).status_code==400


def test_empty_filter_is_backwards_compatible_and_no_match_export_has_header(store):
    assert store.series('1',wallets='')['scope']=='market'
    assert store.series('1',wallets='',wallet_role='maker')['stats']==store.series('1')['stats']
    with TestClient(create_app(str(store.db),str(store.cache))) as client:
        response=client.get('/api/markets/1/export.csv',params={'wallets':'0x'+'d'*40})
        assert response.status_code==200
        assert 'market_id' in response.text and list(csv.DictReader(response.text.splitlines()))==[]
