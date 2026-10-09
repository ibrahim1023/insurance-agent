from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext


class CurrencyError(ValueError):
    pass


def convert_amount(amount, source_currency, output_currency, usd_base_rates, *, minor_units):
    if type(minor_units) is not int or not 0 <= minor_units <= 4:
        raise CurrencyError('unsupported minor-unit metadata')
    if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
        raise CurrencyError('invalid source amount')
    try:
        with localcontext() as context:
            context.prec = 40
            if source_currency == output_currency:
                converted = amount
            else:
                rates = [usd_base_rates.get(code) for code in (source_currency, output_currency)]
                if any(type(rate) not in (int, Decimal) for rate in rates):
                    raise CurrencyError('missing or invalid FX rate')
                source, output = (Decimal(rate) for rate in rates)
                if not source.is_finite() or not output.is_finite() or min(source, output) <= 0:
                    raise CurrencyError('invalid FX rate')
                converted = amount * output / source
            if not converted.is_finite():
                raise CurrencyError('invalid converted amount')
            return converted.quantize(Decimal(1).scaleb(-minor_units), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise CurrencyError('invalid currency conversion') from None
