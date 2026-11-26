import pytest
from app.calculator import calculate_weights, WeightDistributionError

class DummyOffer:
    def __init__(self, status="active", share=0, is_pinned=False):
        self.status = status
        self.share = share
        self.is_pinned = is_pinned

def test_uniform_distribution():
    # 1 offer -> 100%
    offers = [DummyOffer(status="active", share=0, is_pinned=False)]
    calculate_weights(offers)
    assert offers[0].share == 100

    # 2 offers -> 50/50
    offers = [DummyOffer(), DummyOffer()]
    calculate_weights(offers)
    assert [o.share for o in offers] == [50, 50]

    # 3 offers -> 34/33/33
    offers = [DummyOffer(), DummyOffer(), DummyOffer()]
    calculate_weights(offers)
    assert [o.share for o in offers] == [34, 33, 33]

    # 4 offers -> 25/25/25/25
    offers = [DummyOffer(), DummyOffer(), DummyOffer(), DummyOffer()]
    calculate_weights(offers)
    assert [o.share for o in offers] == [25, 25, 25, 25]

def test_pinned_distribution():
    # Pin 25% + two unpinned (75% / 2 -> 38 / 37)
    offers = [
        DummyOffer(status="active", share=25, is_pinned=True),
        DummyOffer(status="active", share=0, is_pinned=False),
        DummyOffer(status="active", share=0, is_pinned=False)
    ]
    calculate_weights(offers)
    assert offers[0].share == 25
    assert offers[0].is_pinned is True
    assert {offers[1].share, offers[2].share} == {38, 37}

def test_all_pinned_valid():
    offers = [
        DummyOffer(status="active", share=50, is_pinned=True),
        DummyOffer(status="active", share=50, is_pinned=True)
    ]
    calculate_weights(offers)
    assert [o.share for o in offers] == [50, 50]

def test_all_pinned_invalid_sum():
    offers = [
        DummyOffer(status="active", share=40, is_pinned=True),
        DummyOffer(status="active", share=40, is_pinned=True)
    ]
    with pytest.raises(WeightDistributionError):
        calculate_weights(offers)

def test_removed_offers():
    offers = [
        DummyOffer(status="active", share=50, is_pinned=False),
        DummyOffer(status="removed", share=50, is_pinned=True)
    ]
    calculate_weights(offers)
    assert offers[0].share == 100
    assert offers[1].share == 0
    assert offers[1].is_pinned is False
