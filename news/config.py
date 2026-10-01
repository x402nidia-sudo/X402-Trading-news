from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
NETWORKS = {
    "mainnet": ("algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=", "31566704"),
    "testnet": ("algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=", "10458941"),
}


def flag(name, default=False):
    value = os.getenv(name, str(default)).lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name}: use true/false")
    return value in {"true", "1"}


def atomic_usdc(value):
    try:
        amount = Decimal(str(value)) * 1_000_000
        if not amount.is_finite() or amount <= 0 or amount > 2**53 - 1 or amount != amount.to_integral_value():
            raise ValueError()
        return str(int(amount))
    except (InvalidOperation, ValueError):
        raise ValueError("PRICE_USDC must be positive with at most 6 decimal places") from None


@dataclass
class Settings:
    demo: bool = False
    payments: bool = False
    public_url: str = "http://127.0.0.1:8000"
    network_name: str = "testnet"
    pay_to: str = ""
    price_usdc: str = "0.2"
    facilitator: str = "https://facilitator.goplausible.xyz"
    admin_token: str = ""
    db_path: str = str(ROOT / "data" / "news.sqlite3")
    cache_seconds: int = 900
    max_age_hours: int = 24
    language: str = "en"
    provider_keys: dict = field(default_factory=dict)
    provider_budgets: dict = field(default_factory=lambda: {"newsapi": 90, "gnews": 90})
    request_limit: int = 60
    email_sender: str = ""
    email_password: str = ""
    alert_interval_hours: float = 0.5
    web_url: str = "https://trading-news-web.onrender.com"
    rss_feed_urls: tuple = ("https://www.coindesk.com/arc/outboundfeeds/rss/",)
    rss_cache_seconds: int = 900

    @property
    def network(self):
        return NETWORKS[self.network_name][0]

    @property
    def asset(self):
        return NETWORKS[self.network_name][1]

    @property
    def amount(self):
        return atomic_usdc(self.price_usdc)

    def validate(self):
        from .ranking import canonical_url
        if not 1 <= len(self.rss_feed_urls) <= 10 or self.rss_cache_seconds < 60:
            raise ValueError("Configure 1–10 RSS feeds and RSS_CACHE_SECONDS >=60")
        for url in self.rss_feed_urls:
            if not canonical_url(url) or urlsplit(url).scheme != "https":
                raise ValueError("RSS_FEED_URLS must contain public HTTPS feed URLs")
        if self.network_name not in NETWORKS:
            raise ValueError("ALGORAND_NETWORK must be mainnet or testnet")
        self.price_usdc = format(Decimal(self.amount) / 1_000_000, "f")
        if self.language not in {"en", "es", "all"}:
            raise ValueError("NEWS_LANGUAGE must be en, es or all")
        if self.cache_seconds < 10 or not 1 <= self.max_age_hours <= 168:
            raise ValueError("CACHE_SECONDS must be >=10 and MAX_AGE_HOURS between 1 and 168")
        if self.request_limit < 1 or any(v < 0 for v in self.provider_budgets.values()):
            raise ValueError("Invalid quotas")
        if not 0.5 <= self.alert_interval_hours <= 168:
            raise ValueError("ALERT_INTERVAL_HOURS must be between 0.5 and 168")
        for value in (self.public_url, self.facilitator, self.web_url):
            parsed = urlsplit(value)
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
                raise ValueError("Public URLs must use HTTPS; HTTP is allowed only for localhost")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("URLs containing credentials or query parameters are not allowed")
        if self.payments:
            from algosdk.encoding import is_valid_address
            if self.demo:
                raise ValueError("Demo content cannot be charged. Set DEMO_MODE=false for x402")
            if not is_valid_address(self.pay_to):
                raise ValueError("PAYTO_ADDRESS must be a valid Algorand address")
            if self.db_path == ":memory:":
                raise ValueError("Payments require persistent storage")
        if self.admin_token and len(self.admin_token) < 24:
            raise ValueError("ADMIN_TOKEN must have at least 24 characters")

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        result = cls(
            demo=flag("DEMO_MODE", False), payments=flag("PAYMENTS_ENABLED"),
            public_url=os.getenv("PUBLIC_BASE_URL", "https://x402-trading-news.onrender.com").rstrip("/"),
            network_name=os.getenv("ALGORAND_NETWORK", "mainnet"),
            pay_to=os.getenv("PAYTO_ADDRESS") or os.getenv("PAY_TO_ALGORAND_ADDRESS") or os.getenv("PAY_TO_ADDRESS", ""),
            price_usdc=os.getenv("PRICE_USDC", "0.2").strip(),
            facilitator=os.getenv("FACILITATOR_URL", "https://facilitator.goplausible.xyz").rstrip("/"),
            admin_token=os.getenv("ADMIN_TOKEN", ""),
            db_path=os.getenv("DATABASE_PATH", str(ROOT / "data" / "news.sqlite3")),
            cache_seconds=int(os.getenv("CACHE_SECONDS", "900")),
            max_age_hours=int(os.getenv("MAX_AGE_HOURS", "24")),
            language=os.getenv("NEWS_LANGUAGE", "en"),
            provider_keys={p: os.getenv(k, "") for p, k in {
                "newsapi": "NEWSAPI_KEY", "gnews": "GNEWS_API_KEY"
            }.items()},
            provider_budgets={p: int(os.getenv(p.upper() + "_DAILY_LIMIT", str(n))) for p, n in
                              {"newsapi": 90, "gnews": 90}.items()},
            request_limit=int(os.getenv("REQUESTS_PER_MINUTE", "60")),
            email_sender=os.getenv("EMAIL_REMITENTE", "").strip(),
            email_password=os.getenv("EMAIL_PASSWORD", "").replace(" ", "").strip(),
            alert_interval_hours=float(os.getenv("ALERT_INTERVAL_HOURS", "0.5")),
            web_url=os.getenv("WEB_BASE_URL", "https://trading-news-web.onrender.com").rstrip("/"),
            rss_feed_urls=tuple(dict.fromkeys(u.strip() for u in os.getenv("RSS_FEED_URLS", "https://www.coindesk.com/arc/outboundfeeds/rss/").split(",") if u.strip())),
            rss_cache_seconds=int(os.getenv("RSS_CACHE_SECONDS", "900")),
        )
        result.validate()
        return result
