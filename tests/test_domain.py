import pytest
from xvi.domain import normalize, identifier, metadata_outcome, choose_level, epoch, clean_json


@pytest.mark.parametrize('token,side,expected', [
    ('token1','BUY',(.2,'BUY')), ('token1','SELL',(.2,'SELL')),
    ('token2','BUY',(.8,'SELL')), ('token2','SELL',(.8,'BUY')),
])
def test_original_semantics(token,side,expected):
    p,direction=normalize(.2,token,side)
    assert p==pytest.approx(expected[0]) and direction==expected[1]


@pytest.mark.parametrize('price', [float('nan'),float('inf'),-.1,1.1])
def test_invalid_prices(price):
    with pytest.raises(ValueError):
        normalize(price,'token1','BUY')


@pytest.mark.parametrize(
    'value',
    ['../secret', "1' OR 1=1", '', 'a/b', 'a'*129],
)
def test_safe_identifiers(value):
    with pytest.raises(ValueError):
        identifier(value)


def test_metadata_is_not_a_settlement_or_probability_estimate():
    assert metadata_outcome('1','["0.99","0.01"]','Yes','No') is None
    assert metadata_outcome('0','["1","0"]','Yes','No') is None
    assert metadata_outcome('1','["1","0"]','Yes','No')=='Yes'
    assert metadata_outcome('true',"['0','1']",'A','B')=='B'
    assert metadata_outcome('1',None,'Yes','No') is None
    assert metadata_outcome('1','__import__("os").system("false")','Yes','No') is None


def test_level_selection_and_serialization():
    assert choose_level(10,86400,100)=='raw'
    assert choose_level(10000,3600,120)=='30s'
    assert epoch('2025-01-01T00:00:00Z')==1735689600
    assert clean_json({'a':float('nan')})=={'a':None}
