from decimal import Decimal

import pytest

from backend.currency import CurrencyError, convert_amount


RATES = {'USD': Decimal('1'), 'AED': Decimal('3.67'), 'GBP': Decimal('0.75')}


@pytest.mark.parametrize('amount,source,target,expected', [
    ('36.70', 'AED', 'USD', '10.00'),
    ('10.00', 'USD', 'AED', '36.70'),
    ('36.70', 'AED', 'GBP', '7.50'),
])
def test_cross_currency_direction_is_correct(amount, source, target, expected):
    assert convert_amount(Decimal(amount), source, target, RATES, minor_units=2) == Decimal(expected)


def test_currency_identity_does_not_require_external_rate():
    assert convert_amount(Decimal('10.005'), 'USD', 'USD', {}, minor_units=2) == Decimal('10.01')


def test_minor_units_are_not_assumed_to_be_two():
    assert convert_amount(Decimal('1234.5'), 'JPY', 'JPY', {}, minor_units=0) == Decimal('1235')
    assert convert_amount(Decimal('1.2345'), 'KWD', 'KWD', {}, minor_units=3) == Decimal('1.235')


@pytest.mark.parametrize('bad', [Decimal('0'), Decimal('-1'), Decimal('NaN'), Decimal('Infinity'), True])
def test_invalid_rates_never_produce_populated_conversion(bad):
    with pytest.raises(CurrencyError):
        convert_amount(Decimal('10'), 'USD', 'AED', {'USD': Decimal('1'), 'AED': bad}, minor_units=2)


def test_missing_rate_is_not_a_guess():
    with pytest.raises(CurrencyError):
        convert_amount(Decimal('10'), 'USD', 'EUR', RATES, minor_units=2)


@pytest.mark.parametrize('bad', [None, -1, True, '2'])
def test_unknown_minor_unit_metadata_does_not_default_to_cents(bad):
    with pytest.raises(CurrencyError):
        convert_amount(Decimal('10'), 'USD', 'USD', {}, minor_units=bad)


@pytest.mark.parametrize('bad', [Decimal('-1'), Decimal('NaN'), Decimal('Infinity'), True])
def test_invalid_amount_is_not_zeroed(bad):
    with pytest.raises(CurrencyError):
        convert_amount(bad, 'USD', 'USD', {}, minor_units=2)
