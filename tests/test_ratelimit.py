"""The limiter is load-bearing: exceeding 10 req/s gets the IP blocked for ~10 min."""

from __future__ import annotations

import pytest

from factline.clients.base import TokenBucket


class FakeClock:
    """Deterministic clock so the test asserts on behaviour, not wall time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_bucket(rate: float = 8.0) -> tuple[TokenBucket, FakeClock]:
    clock = FakeClock()
    return TokenBucket(rate=rate, monotonic=clock.monotonic, sleep=clock.sleep), clock


def test_burst_up_to_capacity_does_not_block():
    bucket, clock = make_bucket(rate=8.0)
    for _ in range(8):
        assert bucket.acquire() == 0.0
    assert clock.slept == []


def test_blocks_once_burst_is_spent():
    bucket, clock = make_bucket(rate=8.0)
    for _ in range(8):
        bucket.acquire()
    waited = bucket.acquire()
    assert waited == pytest.approx(1 / 8)
    assert clock.slept == [pytest.approx(1 / 8)]


def test_sustained_rate_does_not_exceed_configured_rps():
    """Over a long run the effective rate must stay at or under the limit."""
    bucket, clock = make_bucket(rate=8.0)
    requests = 80
    for _ in range(requests):
        bucket.acquire()

    elapsed = clock.now
    # The first `capacity` requests are free, the rest are paced at `rate`.
    assert elapsed == pytest.approx((requests - 8) / 8)
    effective_rps = requests / elapsed
    assert effective_rps <= 10.0, f"{effective_rps:.2f} rps would get us blocked"


def test_tokens_refill_over_idle_time():
    bucket, clock = make_bucket(rate=8.0)
    for _ in range(8):
        bucket.acquire()
    clock.now += 1.0  # one idle second refills the whole bucket
    assert bucket.acquire() == 0.0


def test_rejects_nonsense_configuration():
    with pytest.raises(ValueError):
        TokenBucket(rate=0)
    bucket, _ = make_bucket(rate=8.0)
    with pytest.raises(ValueError):
        bucket.acquire(tokens=999)
