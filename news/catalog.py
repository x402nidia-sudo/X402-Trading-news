import json
from .config import ROOT

ASSETS = {a["symbol"]: a for a in json.loads((ROOT / "assets.json").read_text(encoding="utf-8"))}


def get_asset(symbol):
    symbol = symbol.upper().strip()
    if symbol not in ASSETS:
        raise KeyError(symbol)
    return ASSETS[symbol]


def query_for(asset):
    terms = " OR ".join('"' + a + '"' for a in asset["aliases"][:3])
    if asset.get("ambiguous"):
        return f"({terms}) AND (crypto OR blockchain OR token)"
    return terms
