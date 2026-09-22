"""Small public config; wallet secrets never go in config.txt."""
from configparser import ConfigParser
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
from urllib.parse import urlsplit
from algosdk.encoding import is_valid_address

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = 'https://x402-trading-news.onrender.com'
MERCHANT = 'EH5BHWISPB7MEIITJIWF2VB3YFN2RZLJMWBRV6CBJV76FBAEAALL6XKSQE'
NETWORKS = {
    'mainnet': ('algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=', '31566704'),
    'testnet': ('algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=', '10458941'),
}


def atoms(value):
    try:
        d = Decimal(str(value)) * 1_000_000
        if not d.is_finite() or d <= 0 or d != d.to_integral_value(): raise ValueError()
        return int(d)
    except (ValueError, InvalidOperation):
        raise ValueError('Invalid amount: enter a positive USDC amount.') from None


def amount(value):
    return format(Decimal(value) / 1_000_000, '.6f').rstrip('0').rstrip('.')


def safe_url(value):
    p = urlsplit(value)
    if p.username or p.password or p.query or p.fragment or not p.hostname:
        raise ValueError('Invalid API URL.')
    if p.scheme != 'https' and not (p.scheme == 'http' and p.hostname in {'127.0.0.1','localhost'}):
        raise ValueError('The API must use HTTPS.')
    return value.rstrip('/')


def private_write(path, text):
    path = Path(path);path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt': path.parent.chmod(0o700)
    temporary = path.with_name(path.name + '.new')
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f: f.write(text)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


@dataclass
class Config:
    api_url: str = BASE_URL
    network: str = 'mainnet'
    mode: str = 'pera'
    wallet_address: str = ''
    merchant_address: str = MERCHANT
    max_per_query_usdc: str = '0.20'
    max_per_day_usdc: str = '1.00'
    configured: bool = False

    def validate(self, require_ready=True):
        safe_url(self.api_url)
        if self.network not in NETWORKS or self.mode not in {'pera','automatic'}:
            raise ValueError('Invalid network or payment mode. Run install.py.')
        if not is_valid_address(self.merchant_address): raise ValueError('Invalid service recipient.')
        if atoms(self.max_per_day_usdc) < atoms(self.max_per_query_usdc):
            raise ValueError('The daily budget must cover at least one query.')
        if require_ready and (not self.configured or not is_valid_address(self.wallet_address)):
            raise ValueError('Setup is incomplete. Run INSTALL.bat or bash install.sh.')
        return self

    def save(self, path):
        parser = ConfigParser(interpolation=None)
        parser['tradingnews'] = {k:str(v).lower() if isinstance(v,bool) else str(v) for k,v in self.__dict__.items()}
        import io
        text=io.StringIO();parser.write(text)
        private_write(path, '# Written by the installer. This file does not contain the private key.\n'+text.getvalue())

    @classmethod
    def load(cls, path=ROOT/'config.txt', require_ready=True):
        p=ConfigParser(interpolation=None)
        if not p.read(path,encoding='utf-8'):
            if require_ready: raise ValueError('Run the installer first.')
            return cls()
        s=p['tradingnews']
        fields={k:s.get(k,getattr(cls(),k)) for k in cls.__dataclass_fields__ if k!='configured'}
        return cls(**fields,configured=s.getboolean('configured',False)).validate(require_ready)
