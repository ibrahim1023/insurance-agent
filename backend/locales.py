import re
from functools import lru_cache

import pycountry
from babel.core import get_global
from pydantic import BaseModel, ConfigDict, field_validator

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _clean(value) -> str:
    if not isinstance(value, str):
        raise ValueError("locale code must be a string")
    return value.strip().upper()


def validate_country(value) -> str:
    code = _clean(value)
    if not _COUNTRY_RE.match(code):
        raise ValueError("country must be two ASCII letters")
    if pycountry.countries.get(alpha_2=code) is None:
        raise ValueError(f"unknown ISO 3166 alpha-2 country: {code}")
    return code


@lru_cache(maxsize=1)
def _current_tender_currencies() -> frozenset:
    supported = set()
    for records in get_global("territory_currencies").values():
        for code, _start, end, tender in records:
            if end is None and tender:
                supported.add(code)
    return frozenset(supported)


def validate_currency(value) -> str:
    code = _clean(value)
    if not _CURRENCY_RE.match(code):
        raise ValueError("currency must be three ASCII letters")
    if pycountry.currencies.get(alpha_3=code) is None:
        raise ValueError(f"unknown ISO 4217 alpha-3 currency: {code}")
    if code not in _current_tender_currencies():
        raise ValueError(f"unsupported currency (not a current tender): {code}")
    return code


def currency_minor_units(code: str) -> int:
    normalized = validate_currency(code)
    fractions = get_global("currency_fractions")
    entry = fractions.get(normalized, fractions["DEFAULT"])
    digits = entry[0]
    if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 4:
        raise ValueError(f"unsupported currency precision for {normalized}")
    return digits


class Locale(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    country: str
    currency: str

    @field_validator("country", mode="before")
    @classmethod
    def _check_country(cls, value):
        return validate_country(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _check_currency(cls, value):
        return validate_currency(value)
