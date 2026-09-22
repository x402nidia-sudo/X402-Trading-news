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
        if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            raise ValueError()
        return str(int(amount))
    except (InvalidOperation, ValueError):
        raise ValueError("PRICE_USDC must be positive with no more than 6 decimal places") from None


@dataclass
class Settings:
    demo: bool = True
    payments: bool = False
    public_url: str = "http://127.0.0.1:8000"
    network_name: str = "testnet"
    pay_to: str = ""
    price_usdc: str = "0.20"
    facilitator: str = "https://facilitator.goplausible.xyz"
    admin_token: str = ""
    db_path: str = str(ROOT / "data" / "news.sqlite3")
    cache_seconds: int = 900
    stale_max_age_seconds: int = 3600
    gdelt_enabled: bool = True
    gdelt_interval_seconds: float = 10
    provider_queue_seconds: float = 12
    prefetch_assets: tuple = ()
    max_age_hours: int = 48
    language: str = "en"
    provider_keys: dict = field(default_factory=dict)
    provider_budgets: dict = field(default_factory=lambda: {"gdelt": 5000, "guardian": 450, "newsapi": 90, "gnews": 90})
    ai_enabled: bool = False
    ai_key: str = ""
    ai_model: str = ""
    ai_daily_limit: int = 50
    request_limit: int = 60

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
        if self.network_name not in NETWORKS:
            raise ValueError("ALGORAND_NETWORK must be mainnet or testnet")
        self.amount
        if self.language not in {"en", "es", "all"}:
            raise ValueError("NEWS_LANGUAGE must be en, es or all")
        if self.cache_seconds < 10 or not 1 <= self.max_age_hours <= 168:
            raise ValueError("CACHE_SECONDS must be >=10 and MAX_AGE_HOURS must be between 1 and 168")
        if self.stale_max_age_seconds < self.cache_seconds:
            raise ValueError("STALE_MAX_AGE_SECONDS must be >= CACHE_SECONDS")
        if not 5 <= self.gdelt_interval_seconds <= 300 or not 0 < self.provider_queue_seconds <= 30:
            raise ValueError("GDELT_MIN_INTERVAL_SECONDS must be between 5 and 300; PROVIDER_QUEUE_SECONDS must be between 0 and 30")
        from .catalog import ASSETS
        if any(s not in ASSETS for s in self.prefetch_assets):
            raise ValueError("PREFETCH_ASSETS contains an unknown asset")
        if self.request_limit < 1 or self.ai_daily_limit < 0 or any(v < 0 for v in self.provider_budgets.values()):
            raise ValueError("Invalid quotas")
        for value in (self.public_url, self.facilitator):
            parsed = urlsplit(value)
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
                raise ValueError("Public URLs must use HTTPS; HTTP is only allowed on localhost")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("URLs containing credentials or query parameters are not allowed")
        if self.payments:
            from algosdk.encoding import is_valid_address
            if self.demo:
                raise ValueError("Demo data cannot be sold. Set DEMO_MODE=false for x402")
            if not is_valid_address(self.pay_to):
                raise ValueError("PAYTO_ADDRESS must be a valid Algorand address")
            if self.db_path == ":memory:":
                raise ValueError("Payments require persistent storage")
        if self.admin_token and len(self.admin_token) < 24:
            raise ValueError("ADMIN_TOKEN must contain at least 24 characters")
        if self.ai_enabled and (not self.ai_key or not self.ai_model):
            raise ValueError("AI_RERANK requires OPENAI_API_KEY and OPENAI_MODEL")

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        result = cls(
            demo=flag("DEMO_MODE", False), payments=flag("PAYMENTS_ENABLED"),
            public_url=os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            network_name=os.getenv("ALGORAND_NETWORK", "testnet"),
            pay_to=os.getenv("PAYTO_ADDRESS", ""), price_usdc=os.getenv("PRICE_USDC", "0.20"),
            facilitator=os.getenv("FACILITATOR_URL", "https://facilitator.goplausible.xyz").rstrip("/"),
            admin_token=os.getenv("ADMIN_TOKEN", ""),
            db_path=os.getenv("DATABASE_PATH", str(ROOT / "data" / "news.sqlite3")),
            cache_seconds=int(os.getenv("CACHE_SECONDS", "900")),
            stale_max_age_seconds=int(os.getenv("STALE_MAX_AGE_SECONDS", "3600")),
            gdelt_enabled=flag("GDELT_ENABLED", True),
            gdelt_interval_seconds=float(os.getenv("GDELT_MIN_INTERVAL_SECONDS", "10")),
            provider_queue_seconds=float(os.getenv("PROVIDER_QUEUE_SECONDS", "12")),
            prefetch_assets=tuple(dict.fromkeys(s.strip().upper() for s in
                os.getenv("PREFETCH_ASSETS", "BTC,ETH,SOL,ALGO").split(",") if s.strip())),
            max_age_hours=int(os.getenv("MAX_AGE_HOURS", "48")),
            language=os.getenv("NEWS_LANGUAGE", "en"),
            provider_keys={p: os.getenv(k, "") for p, k in {
                "guardian": "GUARDIAN_API_KEY", "newsapi": "NEWSAPI_KEY", "gnews": "GNEWS_API_KEY"
            }.items()},
            provider_budgets={p: int(os.getenv(p.upper() + "_DAILY_LIMIT", str(n))) for p, n in
                              {"gdelt": 5000, "guardian": 450, "newsapi": 90, "gnews": 90}.items()},
            ai_enabled=flag("AI_RERANK"), ai_key=os.getenv("OPENAI_API_KEY", ""),
            ai_model=os.getenv("OPENAI_MODEL", ""), ai_daily_limit=int(os.getenv("AI_DAILY_LIMIT", "50")),
            request_limit=int(os.getenv("REQUESTS_PER_MINUTE", "60")),
        )
        result.validate()
        return result
