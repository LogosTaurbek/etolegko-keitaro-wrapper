from typing import List, Protocol, Any

class OfferProtocol(Protocol):
    status: str
    share: int
    is_pinned: bool

class WeightDistributionError(Exception):
    pass

def calculate_weights(offers: List[Any]) -> None:
    for o in offers:
        if o.status == "removed":
            o.share = 0
            o.is_pinned = False

    active_list = [o for o in offers if o.status in ("active", "draft_added")]
    if not active_list:
        return

    pinned = [o for o in active_list if o.is_pinned]
    unpinned = [o for o in active_list if not o.is_pinned]

    pinned_sum = sum(o.share for o in pinned)

    if len(unpinned) == 0:
        if pinned_sum == 100:
            return
        else:
            raise WeightDistributionError("Невозможно распределить 100%: все оставшиеся офферы закреплены, снимите Pin")

    if pinned_sum > 100 or (pinned_sum == 100 and len(unpinned) > 0):
        raise WeightDistributionError("Сумма закреплённых весов не оставляет процентов для незапиненных офферов")

    remaining = 100 - pinned_sum
    base_share = remaining // len(unpinned)
    remainder = remaining % len(unpinned)

    for i, o in enumerate(unpinned):
        o.share = base_share + (1 if i < remainder else 0)
