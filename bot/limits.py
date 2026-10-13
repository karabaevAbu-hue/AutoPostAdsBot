"""Антибан-правила. Зашиты в код, Заказчик их отключить не может (ТЗ п.7)."""
import random
from datetime import datetime, timedelta, timezone

MIN_INTERVAL_MIN = 180          # не чаще раза в 3 часа на группу
JITTER = 0.15                   # ±15%
MIN_DELAY_SEC = 40              # пауза между отправками
MAX_DELAY_SEC = 120
HOURLY_CAP = 20                 # глобально на аккаунт
DAILY_CAP = 150
QUIET_START_H = 23              # тихие часы 23:00-08:00 (локальное время)
QUIET_END_H = 8
WARMUP_DAYS = 14
WARMUP_MAX_GROUPS = 5
WARMUP_MIN_INTERVAL_MIN = 12 * 60
MAX_CONSECUTIVE_FAILS = 3
CAPTION_LIMIT = 1024
ALLOWED_INTERVALS_H = (3, 4, 6, 8, 12, 24)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def in_quiet_hours(now_utc: datetime, tz_offset_hours: int) -> bool:
    h = (now_utc + timedelta(hours=tz_offset_hours)).hour
    return h >= QUIET_START_H or h < QUIET_END_H


def quiet_end(now_utc: datetime, tz_offset_hours: int) -> datetime:
    """Ближайшее 08:00 локального времени (в UTC)."""
    local = now_utc + timedelta(hours=tz_offset_hours)
    end = local.replace(hour=QUIET_END_H, minute=0, second=0, microsecond=0)
    if end <= local:
        end += timedelta(days=1)
    return end - timedelta(hours=tz_offset_hours)


def in_warmup(account_created_utc: datetime, now_utc: datetime) -> bool:
    return now_utc - account_created_utc < timedelta(days=WARMUP_DAYS)


def effective_interval_min(ad_interval_min: int, group_max_per_day: int | None,
                           warmup: bool) -> int:
    """Берём самое строгое: выбор Заказчика, правило группы, минимум, прогрев."""
    value = max(ad_interval_min, MIN_INTERVAL_MIN)
    if group_max_per_day and group_max_per_day > 0:
        value = max(value, -(-24 * 60 // group_max_per_day))  # ceil
    if warmup:
        value = max(value, WARMUP_MIN_INTERVAL_MIN)
    return value


def next_run(now_utc: datetime, interval_min: int,
             rng: random.Random | None = None) -> datetime:
    r = rng or random
    shift = interval_min * r.uniform(-JITTER, JITTER)
    return now_utc + timedelta(minutes=interval_min + shift)


def random_pause(rng: random.Random | None = None) -> float:
    return (rng or random).uniform(MIN_DELAY_SEC, MAX_DELAY_SEC)


def caption_ok(text: str) -> bool:
    return 0 < len(text) <= CAPTION_LIMIT
