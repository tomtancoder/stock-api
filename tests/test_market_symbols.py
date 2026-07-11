from app.services.market_symbols import (
    normalize_exchange,
    to_public_symbol,
    to_yahoo_symbol,
)


def test_sgx_symbol_round_trip():
    assert normalize_exchange(" sgx ") == "SGX"
    assert to_yahoo_symbol("SGX", "d05") == "D05.SI"
    assert to_yahoo_symbol("SGX", "D05.SI") == "D05.SI"
    assert to_public_symbol("SGX", "D05") == "SGX:D05"
    assert to_public_symbol("SGX", "D05.SI") == "SGX:D05"


def test_gold_alias_keeps_existing_yahoo_mapping():
    assert to_yahoo_symbol("TVC", "XAUUSD") == "GC=F"
    assert to_public_symbol("TVC", "GOLD") == "TVC:XAUUSD"


def test_yahoo_futures_symbol_is_not_a_public_gold_alias():
    assert to_public_symbol("TVC", "GC=F") == "TVC:GC=F"


def test_normal_symbols_are_uppercased_without_suffixes():
    assert to_yahoo_symbol("nasdaq", "aapl") == "AAPL"
    assert to_public_symbol("nasdaq", "aapl") == "NASDAQ:AAPL"
