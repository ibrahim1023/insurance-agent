import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from backend.currency import convert_amount
from backend.fx import FX_URL, FxClient, FxError, parse_fx_response

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def response_body(**updates):
    value = {
        'result': 'success',
        'base_code': 'USD',
        'time_last_update_unix': int((NOW - timedelta(hours=1)).timestamp()),
        'time_next_update_unix': int((NOW + timedelta(hours=23)).timestamp()),
        'rates': {'USD': 1, 'AED': 3.67, 'GBP': 0.75},
    }
    value.update(updates)
    return json.dumps(value).encode()


def test_dated_fx_preserves_decimal_inputs_and_literal_cross_conversion():
    snapshot = parse_fx_response(response_body(), retrieved_at=NOW)
    assert snapshot.source_url == FX_URL
    assert snapshot.data_at == NOW - timedelta(hours=1)
    assert snapshot.retrieved_at == NOW
    assert snapshot.rates['AED'] == Decimal('3.67')
    assert convert_amount(Decimal('36.70'), 'AED', 'USD', snapshot.rates, minor_units=2) == Decimal('10.00')
    assert convert_amount(Decimal('10'), 'USD', 'AED', snapshot.rates, minor_units=2) == Decimal('36.70')
    assert convert_amount(Decimal('36.70'), 'AED', 'GBP', snapshot.rates, minor_units=2) == Decimal('7.50')


@pytest.mark.parametrize('updates', [
    {'result': 'error'},
    {'base_code': 'AED'},
    {'time_last_update_unix': True},
    {'time_last_update_unix': None},
    {'time_last_update_unix': int((NOW - timedelta(hours=49)).timestamp())},
    {'time_last_update_unix': int((NOW + timedelta(seconds=301)).timestamp())},
    {'rates': {'USD': 2, 'AED': 3.67}},
    {'rates': {'USD': 1, 'AED': True}},
    {'rates': {'USD': 1, 'AED': 0}},
    {'rates': {'USD': 1, 'AED': -1}},
    {'rates': {'USD': 1, 'AED': '3.67'}},
    {'rates': {'USD': 1, 'AED': float('nan')}},
    {'rates': {'USD': 1, 'AED': float('inf')}},
    {'rates': []},
])
def test_invalid_or_stale_fx_cannot_be_admitted(updates):
    with pytest.raises(FxError):
        parse_fx_response(response_body(**updates), retrieved_at=NOW)


@pytest.mark.parametrize('body', [b'[]', b'null', b'{broken', b'{}'])
def test_malformed_fx_has_sanitized_failure(body):
    with pytest.raises(FxError):
        parse_fx_response(body, retrieved_at=NOW)


def test_fx_dates_require_timezone_and_accept_boundary_ages():
    with pytest.raises(FxError):
        parse_fx_response(response_body(), retrieved_at=NOW.replace(tzinfo=None))
    for update in (NOW - timedelta(hours=48), NOW + timedelta(seconds=300)):
        assert parse_fx_response(response_body(time_last_update_unix=int(update.timestamp())), retrieved_at=NOW).data_at == update


def test_cached_feed_reuses_one_fetch_then_refuses_failed_refresh():
    async def scenario():
        wall = [NOW]
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=response_body()) if len(requests) == 1 else httpx.Response(503, text='private-provider-detail')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fx = FxClient(client, wallclock=lambda: wall[0])
            first = await fx.get()
            second = await fx.get()
            assert first is second
            assert len(requests) == 1
            assert str(requests[0].url) == FX_URL
            wall[0] += timedelta(hours=24)
            with pytest.raises(FxError) as exc:
                await fx.get()
            assert 'private-provider-detail' not in str(exc.value)
            assert len(requests) == 2

    asyncio.run(scenario())


def test_concurrent_fx_reads_share_one_request():
    async def scenario():
        calls = []

        async def handler(request):
            calls.append(request)
            await asyncio.sleep(0)
            return httpx.Response(200, content=response_body())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fx = FxClient(client, wallclock=lambda: NOW)
            snapshots = await asyncio.gather(*(fx.get() for _ in range(12)))
            assert len(calls) == 1
            assert all(snapshot is snapshots[0] for snapshot in snapshots)

    asyncio.run(scenario())


def test_provider_redirect_is_not_followed():
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fx = FxClient(client, wallclock=lambda: NOW)
            with pytest.raises(FxError):
                await fx.get()
            assert len(calls) == 1

    asyncio.run(scenario())
