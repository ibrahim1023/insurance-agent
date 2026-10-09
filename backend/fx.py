import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

import httpx

FX_URL = 'https://open.er-api.com/v6/latest/USD'
FX_ATTRIBUTION_URL = 'https://www.exchangerate-api.com'
FX_ATTRIBUTION = 'Rates By Exchange Rate API'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class FxError(ValueError):
    pass


@dataclass(frozen=True)
class FxSnapshot:
    rates: Mapping[str, Decimal]
    data_at: datetime
    retrieved_at: datetime
    cache_until: datetime
    source_url: str = FX_URL
    attribution: str = FX_ATTRIBUTION
    attribution_url: str = FX_ATTRIBUTION_URL


def _reject_constant(value):
    raise ValueError('invalid JSON number')


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _timestamp(value):
    if type(value) is not int:
        raise FxError('FX update timestamp invalid')
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise FxError('FX update timestamp invalid') from None


def parse_fx_response(body: bytes, *, retrieved_at: datetime) -> FxSnapshot:
    if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise FxError('FX retrieval timestamp invalid')
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise FxError('FX response invalid')
    try:
        raw = json.loads(body, parse_float=Decimal, parse_constant=_reject_constant, object_pairs_hook=_unique_pairs)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise FxError('FX response invalid') from None
    if not isinstance(raw, dict) or raw.get('result') != 'success' or raw.get('base_code') != 'USD':
        raise FxError('FX source or base invalid')
    data_at = _timestamp(raw.get('time_last_update_unix'))
    age = (retrieved_at - data_at).total_seconds()
    if not -300 <= age <= 48 * 3600:
        raise FxError('FX data stale or future-dated')
    raw_rates = raw.get('rates')
    if not isinstance(raw_rates, dict) or not raw_rates:
        raise FxError('FX rates invalid')
    rates = {}
    for currency, value in raw_rates.items():
        if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency) or type(value) not in (int, Decimal):
            raise FxError('FX rates invalid')
        rate = Decimal(value)
        if not rate.is_finite() or rate <= 0:
            raise FxError('FX rates invalid')
        rates[currency] = rate
    if rates.get('USD') != Decimal('1'):
        raise FxError('FX USD base rate invalid')
    cache_until = min(retrieved_at + timedelta(hours=24), data_at + timedelta(hours=48))
    if 'time_next_update_unix' in raw:
        cache_until = min(cache_until, _timestamp(raw['time_next_update_unix']))
    return FxSnapshot(MappingProxyType(rates), data_at, retrieved_at, cache_until)


class FxClient:
    def __init__(self, client: httpx.AsyncClient, *, wallclock=None):
        self._client = client
        self._wallclock = wallclock or (lambda: datetime.now(timezone.utc))
        self._cache: FxSnapshot | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> FxSnapshot:
        async with self._lock:
            now = self._wallclock()
            cached = self._cache
            if cached is not None and cached.retrieved_at <= now < cached.cache_until and (now - cached.data_at).total_seconds() >= -300:
                return cached
            try:
                async with self._client.stream('GET', FX_URL, timeout=10.0, follow_redirects=False) as response:
                    if response.status_code != 200:
                        raise FxError('FX provider unavailable')
                    chunks = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise FxError('FX response too large')
                        chunks.append(chunk)
            except httpx.HTTPError:
                raise FxError('FX provider unavailable') from None
            snapshot = parse_fx_response(b''.join(chunks), retrieved_at=self._wallclock())
            self._cache = snapshot
            return snapshot
