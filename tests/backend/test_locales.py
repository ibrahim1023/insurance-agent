import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import server
from backend.locales import Locale, currency_minor_units

ORIGIN = "http://localhost:8001"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = server.create_app(tmp_path / "runs", allowed_origin=ORIGIN)
    with TestClient(app) as c:
        yield c


def post(client, path, **kwargs):
    return client.post(path, headers={"origin": ORIGIN}, **kwargs)


def test_locale_normalizes_ascii_independently():
    loc = Locale(country=" ae ", currency=" usd ")
    assert loc.country == "AE"
    assert loc.currency == "USD"


def test_legal_independent_pairs():
    assert Locale(country="AE", currency="AED").currency == "AED"
    assert Locale(country="US", currency="USD").country == "US"
    assert Locale(country="AE", currency="USD").currency == "USD"
    assert Locale(country="GB", currency="AED").country == "GB"


def test_locale_immutable_and_closed():
    loc = Locale(country="AE", currency="USD")
    with pytest.raises(ValidationError):
        loc.country = "US"
    with pytest.raises(ValidationError):
        Locale(country="AE", currency="USD", extra_field=1)


def test_unknown_or_non_money_codes_rejected():
    with pytest.raises(ValidationError):
        Locale(country="ZZ", currency="USD")
    with pytest.raises(ValidationError):
        Locale(country="AE", currency="ZZZ")
    with pytest.raises(ValidationError):
        Locale(country="AE", currency="XXX")
    with pytest.raises(ValidationError):
        Locale(country="AE", currency="XAU")


def test_currency_minor_units():
    assert currency_minor_units("JPY") == 0
    assert currency_minor_units("KWD") == 3
    for code in ("AED", "USD", "EUR", "GBP"):
        assert currency_minor_units(code) == 2
    assert currency_minor_units(" jpy ") == 0


def test_currency_minor_units_rejects_unknown():
    with pytest.raises(ValueError):
        currency_minor_units("ZZZ")
    with pytest.raises(ValueError):
        currency_minor_units("XX")
    with pytest.raises(ValueError):
        currency_minor_units("XAU")


def test_start_sweep_rejects_invalid_iso_codes(client):
    for body in (
        {"country": "ZZ", "currency": "USD", "device": "d"},
        {"country": "AE", "currency": "ZZZ", "device": "d"},
        {"country": "AE", "currency": "XAU", "device": "d"},
    ):
        resp = post(client, "/api/sweeps", json=body)
        assert resp.status_code == 422, (body, resp.text)
    store = client.app.state.store
    assert not any(store.root.iterdir())
    resp = post(
        client,
        "/api/sweeps",
        json={"country": "AE", "currency": "USD", "device": "d"},
    )
    assert resp.status_code == 200
    assert resp.json()["country"] == "AE"
    assert resp.json()["currency"] == "USD"
