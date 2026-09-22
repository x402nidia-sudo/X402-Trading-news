"""Explainable prioritisation of headlines; never a buy/sell signal."""
from datetime import datetime, timezone
import hashlib
import html
import math
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CRYPTO = r"\b(crypto\w*|blockchain|token\w*|defi|bitcoin|ethereum|stablecoin\w*|wallet\w*|staking|criptomoneda\w*)\b"
AMBIGUOUS_TICKERS = {"OP", "COMP", "LINK", "BAL", "SAND", "DASH", "ATOM", "DOT", "GRT", "ONE", "NEAR", "ALGO"}
PRIORITY_DOMAINS = {"reuters.com": .9, "apnews.com": .9, "theguardian.com": .85,
                    "bbc.com": .85, "bbc.co.uk": .85, "ft.com": .85,
                    "coindesk.com": .8, "cointelegraph.com": .7,
                    "sec.gov": .95, "federalreserve.gov": .95, "ecb.europa.eu": .95}
EVENTS = {
    "security": (1., r"\b(hack\w*|exploit\w*|breach|stolen|vulnerability|hackeo|robo)\b"),
    "regulation": (.95, r"\b(regulat\w*|sec|lawsuit|court|ban|etf|approval|aprob\w*|regulaci\w*)\b"),
    "protocol": (.8, r"\b(outage|upgrade|mainnet|fork|halt|interrupci\w*|actualizaci\w*)\b"),
    "adoption": (.65, r"\b(adoption|partnership|launch|integrat\w*|alianza|adopci\w*)\b"),
    "macro": (.7, r"\b(inflation|interest rate|federal reserve|fed|inflaci\w*)\b"),
}
STOP = set("the a an and or in of for to at on is are as by with from after says new news el la de del y en un una para por tras que los las".split())


def clean_text(value, limit=400):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", str(value or "")))).strip()[:limit]


def canonical_url(value):
    try:
        p = urlsplit(value)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        qs = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and
              k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}]
        return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", urlencode(sorted(qs)), ""))
    except (ValueError, TypeError):
        return None


def publisher_domain(url):
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    # Collapse publisher subdomains; covers the common two-part public suffixes.
    tail = 3 if ".".join(parts[-2:]) in {"co.uk", "com.au", "co.in", "com.br", "co.jp"} else 2
    return ".".join(parts[-tail:])


def parse_date(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def contains(text, phrase):
    return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.I))


def relevance(article, asset):
    title, desc = article["title"], article["summary"]
    text = title + " " + desc
    context = bool(re.search(CRYPTO, text, re.I))
    if asset.get("ambiguous") and not context:
        return 0
    title_match = any(contains(title, term) for term in asset["aliases"])
    body_match = any(contains(desc, term) for term in asset["aliases"])
    ticker = asset["symbol"]
    ticker_match = bool(re.search(r"(?<!\w)\$?" + re.escape(ticker) + r"(?!\w)", text))
    if ticker in AMBIGUOUS_TICKERS and not context:
        ticker_match = False
    if title_match:
        return 1.
    if body_match:
        return .72
    return .8 if ticker_match and context else 0


def tokens(title):
    return {x for x in re.findall(r"\w+", title.casefold()) if len(x) > 2 and x not in STOP}


def similar(left, right):
    a, b = tokens(left), tokens(right)
    if a == b:
        return True
    # Don't collapse a rejection with an approval, or negation with affirmation.
    opposites = [r"\b(reject\w*|denie\w*|rechaz\w*)", r"\b(approv\w*|aprob\w*)", r"\b(not|no|never)\b"]
    if any(bool(re.search(p, left, re.I)) != bool(re.search(p, right, re.I)) for p in opposites):
        return False
    return len(a & b) >= 4 and len(a & b) / max(1, len(a | b)) >= .72


def rank_articles(raw, asset, max_age_hours=48, now=None):
    now = now or datetime.now(timezone.utc)
    eligible = []
    # Known publication dates take precedence over a recent discovery of the same URL.
    publications = {}
    for item in raw:
        url, published = canonical_url(item.get("url")), parse_date(item.get("published_at"))
        if url and published and item.get("provider") != "gdelt":
            previous = publications.get(url)
            if not previous or published < previous[0]:
                publications[url] = (published, item["provider"])
    excluded = {"irrelevant": 0, "old_or_undated": 0, "invalid": 0}
    for item in raw:
        url = canonical_url(item.get("url"))
        dt = parse_date(item.get("published_at"))
        date_provider = item.get("provider")
        if not dt and item.get("provider") == "gdelt" and url in publications:
            dt, date_provider = publications[url]
        observed = parse_date(item.get("observed_at")) if item.get("provider") == "gdelt" else None
        published = dt
        dt = dt or observed
        title = clean_text(item.get("title"), 240)
        if not url or not title:
            excluded["invalid"] += 1
            continue
        if not dt or (now - dt).total_seconds() < -300 or (now - dt).total_seconds() > max_age_hours * 3600:
            excluded["old_or_undated"] += 1
            continue
        article = {"id": hashlib.sha256(url.encode()).hexdigest()[:20], "title": title,
                   "url": url, "summary": clean_text(item.get("summary"), 320),
                   "source": clean_text(item.get("source"), 100), "domain": publisher_domain(url),
                   "published_at": published.isoformat() if published else None,
                   "observed_at": observed.isoformat() if observed else None,
                   "effective_at": dt.isoformat(), "date_basis": "publication" if published else "first_seen",
                   "date_provider": date_provider,
                   "providers": [item["provider"]],
                   "coverage": [{"url": url, "source": clean_text(item.get("source"), 100), "domain": publisher_domain(url)}]}
        rel = relevance(article, asset)
        if not rel:
            excluded["irrelevant"] += 1
            continue
        age = max(0, (now - dt).total_seconds() / 3600)
        text = title + " " + article["summary"]
        events = [(name, weight) for name, (weight, pattern) in EVENTS.items() if re.search(pattern, text, re.I)]
        event, weight = max(events, key=lambda x: x[1]) if events else ("market", .35)
        speculative = bool(re.search(r"\b(rumou?r|prediction|could|might|may|rumor|podr[ií]a|predicci[oó]n)\b", title, re.I))
        article.update({"age_hours": round(age, 2), "category": event, "speculative": speculative,
                        "components": {"relevance": round(35 * rel, 2),
                                       "freshness": round(25 * math.exp(-age / 18), 2),
                                       "source_priority": round(15 * PRIORITY_DOMAINS.get(article["domain"], .55), 2),
                                       "event": round(15 * weight * (.5 if speculative else 1), 2)}})
        eligible.append(article)
    eligible.sort(key=lambda a: (-sum(a["components"].values()), a["url"]))
    unique = []
    for article in eligible:
        group = next((a for a in unique if a["url"] == article["url"] or similar(a["title"], article["title"])), None)
        if group:
            group["providers"] = sorted(set(group["providers"] + article["providers"]))
            if article["url"] not in {x["url"] for x in group["coverage"]}:
                group["coverage"].extend(article["coverage"])
        else:
            unique.append(article)
    for article in unique:
        domains = set(x["domain"] for x in article["coverage"])
        article["coverage_domains"] = len(domains)
        article["components"]["coverage"] = min(10, 5 * (len(domains) - 1))
        article["score"] = round(sum(article["components"].values()), 2)
        article["selection_reasons"] = [
            f"Relevance to {asset['symbol']}: {article['components']['relevance']}/35",
            ("Time since publication: " if article['date_basis'] == 'publication' else "Time since GDELT detection (publication unverified): ") + f"{article['age_hours']} hours",
            f"Event type: {article['category']}",
            f"Similar headlines across {len(domains)} domain(s); this is not independent verification",
        ]
        if article["speculative"]:
            article["selection_reasons"].append("Speculative headline: reduced priority")
    unique.sort(key=lambda a: (-a["score"], -parse_date(a["effective_at"]).timestamp(), a["url"]))
    return unique, {"received": len(raw), "eligible": len(eligible), "unique": len(unique),
                    "duplicates": len(eligible) - len(unique), "excluded": excluded}
