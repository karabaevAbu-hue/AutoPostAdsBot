import random
from datetime import datetime, timedelta, timezone

from bot import limits as L

UTC = timezone.utc


def test_min_interval_enforced():
    assert L.effective_interval_min(60, None, False) == 180


def test_group_rule_stricter():
    assert L.effective_interval_min(180, 1, False) == 1440
    assert L.effective_interval_min(180, 2, False) == 720
    assert L.effective_interval_min(180, 3, False) == 480


def test_warmup_interval():
    assert L.effective_interval_min(180, None, True) == 720


def test_quiet_hours_bishkek():
    # 20:00 UTC = 02:00 GMT+6 -> тихие часы
    assert L.in_quiet_hours(datetime(2026, 1, 1, 20, 0, tzinfo=UTC), 6)
    # 06:00 UTC = 12:00 GMT+6 -> можно
    assert not L.in_quiet_hours(datetime(2026, 1, 1, 6, 0, tzinfo=UTC), 6)


def test_quiet_end():
    now = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)  # 02:00 local
    assert L.quiet_end(now, 6) == datetime(2026, 1, 2, 2, 0, tzinfo=UTC)


def test_jitter_within_15_percent():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    rng = random.Random(1)
    for _ in range(200):
        d = (L.next_run(now, 240, rng) - now) / timedelta(minutes=1)
        assert 240 * 0.85 <= d <= 240 * 1.15


def test_warmup_window():
    created = datetime(2026, 1, 1, tzinfo=UTC)
    assert L.in_warmup(created, created + timedelta(days=13))
    assert not L.in_warmup(created, created + timedelta(days=15))


def test_caption_limit():
    assert L.caption_ok("x" * 1024) and not L.caption_ok("x" * 1025) and not L.caption_ok("")
