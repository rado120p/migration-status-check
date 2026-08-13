"""Striktni JSON pro nekonecne floaty.

json.dumps s vychozim allow_nan=True vypise -inf jako '-Infinity' - token,
ktery JSON spec nezna a striktni parsery (jq, jiny jazyk) odmitnou. Realny
zdroj nekonecna jsou opticke urovne nepripojeneho portu (rx/tx '-Inf' od
krabice), takze se nekonecno serializuje jako string token a pri nacteni
snapshotu zase vraci na float - checky vidi porad cislo.

Dekodovani je zamerne uzke: prevadi jen presne tokeny '-Inf'/'Inf'/'NaN'
ve VALUE pozici (klice se nemeni). Zadny collector takove stringy jako
data nevydava, takze kolize nehrozi.
"""

from __future__ import annotations

import math
from typing import Any

_TOKENS = {"Inf": math.inf, "-Inf": -math.inf, "NaN": math.nan}


def encode_nonfinite(value: Any) -> Any:
    """Nahrazuje nekonecne floaty string tokeny, rekurzivne."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Inf" if value > 0 else "-Inf"
    if isinstance(value, dict):
        return {key: encode_nonfinite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [encode_nonfinite(item) for item in value]
    return value


def decode_nonfinite(value: Any) -> Any:
    """Vraci tokeny z encode_nonfinite zpet na floaty, rekurzivne."""
    if isinstance(value, str):
        return _TOKENS.get(value, value)
    if isinstance(value, dict):
        return {key: decode_nonfinite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_nonfinite(item) for item in value]
    return value
