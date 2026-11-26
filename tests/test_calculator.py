import pytest
from app.calculator import WeightDistributionError, calculate_weights


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


def test_pinned_distribution_and_stability():
    # Pin 25% + two unpinned -> 25 / 38 / 37
    pinned = DummyOffer(status="active", share=25, is_pinned=True)
    free1 = DummyOffer(status="active", share=0, is_pinned=False)
    free2 = DummyOffer(status="active", share=0, is_pinned=False)
    offers = [pinned, free1, free2]
    calculate_weights(offers)
    assert pinned.share == 25
    assert pinned.is_pinned is True
    assert [free1.share, free2.share] == [38, 37]

    # Adding another unpinned offer keeps 25% intact -> 25 / 25 / 25 / 25
    free3 = DummyOffer(status="draft_added", share=0, is_pinned=False)
    offers.append(free3)
    calculate_weights(offers)
    assert pinned.share == 25
    assert [free1.share, free2.share, free3.share] == [25, 25, 25]

    # Removing one unpinned offer keeps 25% intact -> 25 / 38 / 37
    free3.status = "removed"
    calculate_weights(offers)
    assert pinned.share == 25
    assert free3.share == 0
    assert [free1.share, free2.share] == [38, 37]


def test_all_pinned_valid():
    offers = [
        DummyOffer(status="active", share=50, is_pinned=True),
        DummyOffer(status="active", share=50, is_pinned=True),
    ]
    calculate_weights(offers)
    assert [o.share for o in offers] == [50, 50]


def test_pinned_edge_cases_raise_error():
    # Removing last free offer when pinned_sum < 100
    offers = [
        DummyOffer(status="active", share=25, is_pinned=True),
        DummyOffer(status="removed", share=75, is_pinned=False),
    ]
    with pytest.raises(WeightDistributionError):
        calculate_weights(offers)

    # All pinned sum != 100
    offers = [
        DummyOffer(status="active", share=40, is_pinned=True),
        DummyOffer(status="active", share=40, is_pinned=True),
    ]
    with pytest.raises(WeightDistributionError):
        calculate_weights(offers)

    # Pinned sum > 100 with unpinned offers
    offers = [
        DummyOffer(status="active", share=60, is_pinned=True),
        DummyOffer(status="active", share=50, is_pinned=True),
        DummyOffer(status="active", share=0, is_pinned=False),
    ]
    with pytest.raises(WeightDistributionError):
        calculate_weights(offers)

    # Pinned sum == 100 leaving 0% for unpinned offer
    offers = [
        DummyOffer(status="active", share=100, is_pinned=True),
        DummyOffer(status="active", share=0, is_pinned=False),
    ]
    with pytest.raises(WeightDistributionError):
        calculate_weights(offers)


def test_removed_and_empty_offers():
    offers = [
        DummyOffer(status="active", share=50, is_pinned=False),
        DummyOffer(status="removed", share=50, is_pinned=True),
    ]
    calculate_weights(offers)
    assert offers[0].share == 100
    assert offers[1].share == 0
    assert offers[1].is_pinned is False

    # All removed -> returns cleanly
    offers = [DummyOffer(status="removed", share=100, is_pinned=True)]
    calculate_weights(offers)
    assert offers[0].share == 0
    assert offers[0].is_pinned is False
