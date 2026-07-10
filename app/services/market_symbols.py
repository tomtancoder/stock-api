def normalize_exchange(exchange: str) -> str:
    return exchange.strip().upper()


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def to_yahoo_symbol(exchange: str, symbol: str) -> str:
    venue = normalize_exchange(exchange)
    normalized = _normalize_symbol(symbol)
    if venue in {"TVC", "CAPITALCOM"} and normalized in {
        "XAUUSD",
        "GOLD",
        "TVC:GOLD",
    }:
        return "GC=F"
    if venue == "SGX" and not normalized.endswith(".SI"):
        return f"{normalized}.SI"
    return normalized


def to_public_symbol(exchange: str, symbol: str) -> str:
    venue = normalize_exchange(exchange)
    normalized = _normalize_symbol(symbol)
    if venue == "SGX" and normalized.endswith(".SI"):
        normalized = normalized[:-3]
    if venue in {"TVC", "CAPITALCOM"} and normalized in {
        "GOLD",
        "TVC:GOLD",
    }:
        normalized = "XAUUSD"
    return f"{venue}:{normalized}"
