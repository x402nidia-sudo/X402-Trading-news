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


def grouped_queries(limit):
    """A few OR queries covering every asset's search terms within a provider's query-length limit."""
    queries = []
    for ambiguous, head, tail in ((False, "", ""), (True, "(", ") AND (crypto OR blockchain OR token)")):
        terms = []
        for asset in ASSETS.values():
            if bool(asset.get("ambiguous")) != ambiguous:
                continue
            for alias in asset["aliases"][:3]:
                term = '"' + alias + '"'
                if terms and len(head + " OR ".join(terms + [term]) + tail) > limit:
                    queries.append(head + " OR ".join(terms) + tail)
                    terms = []
                terms.append(term)
        if terms:
            queries.append(head + " OR ".join(terms) + tail)
    return queries
