"""Confirmed, recurring asset alerts; SQLite + Gmail SMTP, one application worker."""
import asyncio
import hashlib
import html
import json
import logging
import re
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.parse import urlencode
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from .catalog import ASSETS
from .service import NoProviders

LOG = logging.getLogger("uvicorn.error")
COPY = {
    "en": {"confirm": "Confirm news alerts", "confirm_body": "Confirm that you want email alerts when new relevant news is available for {symbol}. If you did not request this, ignore this email. This link expires in 24 hours.", "news": "New relevant news for {symbol}", "intro": "New relevant stories are available. Open Trading News to see availability and buy the complete report if you wish. This alert is free and does not make a purchase.", "open": "Open Trading News", "unsubscribe": "Unsubscribe", "done_confirm": "Subscription confirmed. We will check for new relevant news every {minutes} minutes, subject to source availability. You can close this page.", "done_unsubscribe": "You have been unsubscribed from this asset's alerts.", "error": "The link is invalid, expired or temporarily unavailable. Try again or request a new email on the website.", "privacy": "Your email is used only for these asset alerts. You can unsubscribe at any time.", "action": "Select the button to continue."},
    "es": {"confirm": "Confirmar avisos de noticias", "confirm_body": "Confirma que quieres recibir avisos cuando haya noticias nuevas relevantes para {symbol}. Si no lo has solicitado, ignora este correo. El enlace caduca en 24 horas.", "news": "Nuevas noticias relevantes de {symbol}", "intro": "Hay nuevas noticias relevantes. Abre Trading News para consultar la disponibilidad y comprar el informe completo si lo deseas. Este aviso es gratuito y no realiza ninguna compra.", "open": "Abrir Trading News", "unsubscribe": "Dar de baja", "done_confirm": "Suscripción confirmada. Buscaremos nuevas noticias relevantes cada {minutes} minutos, según la disponibilidad de las fuentes. Puedes cerrar esta página.", "done_unsubscribe": "Se ha cancelado tu suscripción a los avisos de esta moneda.", "error": "El enlace no es válido, ha caducado o el servicio no está disponible. Inténtalo de nuevo o solicita otro correo en la web.", "privacy": "Tu email se utiliza solo para estos avisos de la moneda. Puedes darte de baja cuando quieras.", "action": "Pulsa el botón para continuar."},
    "fr": {"confirm": "Confirmer les alertes", "confirm_body": "Confirmez que vous souhaitez recevoir des alertes pour les nouvelles actualités pertinentes de {symbol}. Si vous ne les avez pas demandées, ignorez cet e-mail. Le lien expire dans 24 heures.", "news": "Nouvelles actualités pertinentes : {symbol}", "intro": "De nouvelles actualités sont disponibles. Ouvrez Trading News pour consulter la disponibilité et acheter le rapport complet si vous le souhaitez. Cette alerte est gratuite et ne déclenche aucun achat.", "open": "Ouvrir Trading News", "unsubscribe": "Se désabonner", "done_confirm": "Abonnement confirmé. Nous rechercherons les nouvelles actualités toutes les {minutes} minutes, selon la disponibilité des sources. Vous pouvez fermer cette page.", "done_unsubscribe": "Vous êtes désabonné des alertes pour cet actif.", "error": "Le lien est invalide, expiré ou temporairement indisponible. Réessayez ou demandez un nouvel e-mail sur le site.", "privacy": "Votre e-mail sert uniquement aux alertes de cet actif. Vous pouvez vous désabonner à tout moment.", "action": "Cliquez sur le bouton pour continuer."},
    "de": {"confirm": "Nachrichtenbenachrichtigungen bestätigen", "confirm_body": "Bestätigen Sie E-Mail-Benachrichtigungen über neue relevante Nachrichten zu {symbol}. Falls Sie diese nicht angefordert haben, ignorieren Sie diese E-Mail. Der Link läuft nach 24 Stunden ab.", "news": "Neue relevante Nachrichten zu {symbol}", "intro": "Neue relevante Nachrichten sind verfügbar. Öffnen Sie Trading News, um die Verfügbarkeit zu prüfen und bei Bedarf den vollständigen Bericht zu kaufen. Diese Benachrichtigung ist kostenlos und löst keinen Kauf aus.", "open": "Trading News öffnen", "unsubscribe": "Abmelden", "done_confirm": "Abonnement bestätigt. Wir prüfen alle {minutes} Minuten auf neue relevante Nachrichten, abhängig von der Verfügbarkeit der Quellen. Sie können diese Seite schließen.", "done_unsubscribe": "Sie wurden von den Benachrichtigungen zu diesem Asset abgemeldet.", "error": "Der Link ist ungültig, abgelaufen oder vorübergehend nicht verfügbar. Versuchen Sie es erneut oder fordern Sie auf der Website eine neue E-Mail an.", "privacy": "Ihre E-Mail wird nur für diese Asset-Benachrichtigungen verwendet. Sie können sich jederzeit abmelden.", "action": "Klicken Sie zum Fortfahren auf die Schaltfläche."},
}


def normalize_email(value):
    value = value.strip()
    # Conservative ASCII mailbox syntax; excludes CR/LF and header injection.
    if len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251})\.[A-Za-z]{2,63}", value):
        raise ValueError("INVALID_EMAIL")
    local, domain = value.rsplit("@", 1)
    if local.startswith(".") or local.endswith(".") or ".." in value or any(x.startswith("-") or x.endswith("-") for x in domain.split(".")):
        raise ValueError("INVALID_EMAIL")
    return value.lower()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Alerts:
    def __init__(self, settings, store, news):
        self.cfg, self.store, self.news = settings, store, news
        self.stop = asyncio.Event()
        self.tick_lock = asyncio.Lock()
        self.mail_lock = asyncio.Lock()
        self.enabled = bool(settings.email_sender and settings.email_password)
        if self.enabled:
            try:
                normalize_email(settings.email_sender)
            except ValueError:
                self.enabled = False
                LOG.error("EMAIL_REMITENTE is invalid; email alerts disabled")
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS news_subscriptions (
                    id INTEGER PRIMARY KEY, email TEXT NOT NULL, symbol TEXT NOT NULL,
                    language TEXT NOT NULL, confirm_hash TEXT UNIQUE, unsubscribe_token TEXT UNIQUE NOT NULL,
                    created REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
                    confirmation_at REAL NOT NULL, last_checked REAL NOT NULL DEFAULT 0,
                    UNIQUE(email,symbol));
                CREATE TABLE IF NOT EXISTS news_alert_deliveries (
                    subscription_id INTEGER NOT NULL, article_id TEXT NOT NULL,
                    state TEXT NOT NULL, updated REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(subscription_id,article_id));
            """)

    def url(self, action, token, language):
        # Fragment keeps the bearer token out of ordinary HTTP access logs.
        return self.cfg.public_url + "/alerts/" + action + "?lang=" + language + "#" + token

    def send_smtp(self, recipient, subject, text):
        message = EmailMessage()
        message["From"] = self.cfg.email_sender
        message["To"] = recipient
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(domain=self.cfg.email_sender.rsplit("@", 1)[-1])
        message.set_content(text)
        # SSL connection and app password; credentials never reach the website or logs.
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15, context=ssl.create_default_context()) as smtp:
            smtp.login(self.cfg.email_sender, self.cfg.email_password)
            smtp.send_message(message)

    async def send(self, recipient, subject, body):
        async with self.mail_lock:
            if not self.store.budget("alert_emails", 350):
                raise HTTPException(429, "EMAIL_LIMIT")
            await asyncio.to_thread(self.send_smtp, recipient, subject, body)

    async def subscribe(self, email, symbol, language, client):
        if not self.enabled:
            raise HTTPException(503, "EMAIL_NOT_CONFIGURED")
        try:
            email = normalize_email(email)
        except ValueError:
            raise HTTPException(422, "INVALID_EMAIL") from None
        if symbol not in ASSETS or language not in COPY:
            raise HTTPException(422, "INVALID_SUBSCRIPTION")
        now = time.time()
        with self.store.connect() as db:
            previous = db.execute("SELECT * FROM news_subscriptions WHERE email=? AND symbol=?", (email, symbol)).fetchone()
            # No change of an existing verified subscription by an unauthenticated visitor.
            if previous and (previous["confirmed"] or now - previous["confirmation_at"] < 3600):
                return {"status": "confirmation_requested"}
        if not self.store.budget("alert_signup_ip:" + digest(client), 10) or not self.store.budget("alert_signup_email:" + digest(email), 3):
            raise HTTPException(429, "EMAIL_LIMIT")
        token = secrets.token_urlsafe(32)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Recheck after acquiring the lock for simultaneous requests.
            previous = db.execute("SELECT * FROM news_subscriptions WHERE email=? AND symbol=?", (email, symbol)).fetchone()
            if previous and (previous["confirmed"] or now - previous["confirmation_at"] < 3600):
                return {"status": "confirmation_requested"}
            if not previous and db.execute("SELECT COUNT(*) FROM news_subscriptions").fetchone()[0] >= 10000:
                raise HTTPException(503, "EMAIL_LIMIT")
            db.execute("""INSERT INTO news_subscriptions
                (email,symbol,language,confirm_hash,unsubscribe_token,created,confirmation_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(email,symbol) DO UPDATE SET
                language=excluded.language,confirm_hash=excluded.confirm_hash,confirmation_at=excluded.confirmation_at""",
                (email, symbol, language, digest(token), secrets.token_urlsafe(32), now, now))
        copy = COPY[language]
        body = copy["confirm_body"].format(symbol=symbol) + "\n\n" + self.url("confirm", token, language) + "\n\n" + copy["privacy"]
        try:
            await self.send(email, copy["confirm"] + " · " + symbol, body)
        except Exception as exc:
            with self.store.connect() as db:
                db.execute("UPDATE news_subscriptions SET confirmation_at=0 WHERE confirm_hash=? AND confirmed=0", (digest(token),))
            LOG.warning("Confirmation email failed (%s)", type(exc).__name__)
            raise HTTPException(503, "EMAIL_UNAVAILABLE") from None
        return {"status": "confirmation_requested"}

    def confirm(self, token):
        if not self.enabled:
            raise HTTPException(503, "EMAIL_NOT_CONFIGURED")
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM news_subscriptions WHERE confirm_hash=?", (digest(token),)).fetchone()
            if not row or time.time() - row["confirmation_at"] > 86400:
                raise HTTPException(400, "INVALID_ALERT_LINK")
            db.execute("UPDATE news_subscriptions SET confirmed=1 WHERE id=?", (row["id"],))
        return {"status": "confirmed"}

    def unsubscribe(self, token):
        with self.store.connect() as db:
            row = db.execute("SELECT id FROM news_subscriptions WHERE unsubscribe_token=?", (token,)).fetchone()
            if row:
                db.execute("DELETE FROM news_alert_deliveries WHERE subscription_id=?", (row["id"],))
                db.execute("DELETE FROM news_subscriptions WHERE id=?", (row["id"],))
        return {"status": "unsubscribed"}

    async def notify(self, subscription, report):
        # Claim before SMTP. An ambiguous network interruption is never auto-retried:
        # SMTP cannot guarantee exactly-once delivery across a process crash.
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM news_subscriptions WHERE id=? AND confirmed=1", (subscription["id"],)).fetchone():
                return
            seen = {r["article_id"]: r for r in db.execute("SELECT * FROM news_alert_deliveries WHERE subscription_id=?", (subscription["id"],))}
            fresh = [a for a in report["articles"] if a["id"] not in seen or (seen[a["id"]]["state"] == "retry" and seen[a["id"]]["attempts"] < 3)]
            fresh = fresh[:10]
            if not fresh:
                return
            for article in fresh:
                db.execute("""INSERT INTO news_alert_deliveries VALUES (?,?,'sending',?,1)
                    ON CONFLICT(subscription_id,article_id) DO UPDATE SET state='sending',updated=excluded.updated,attempts=attempts+1""",
                    (subscription["id"], article["id"], now))
        copy = COPY[subscription["language"]]
        web = self.cfg.web_url + "/?" + urlencode({"asset": subscription["symbol"], "lang": subscription["language"]})
        lines = [copy["intro"], "", copy["open"] + ": " + web, ""]
        # Alerts announce availability only. Today's headlines/content remain paid.
        lines.extend([copy["privacy"], copy["unsubscribe"] + ": " + self.url("unsubscribe", subscription["unsubscribe_token"], subscription["language"])])
        state = "sent"
        try:
            await self.send(subscription["email"], copy["news"].format(symbol=subscription["symbol"]), "\n".join(lines))
        except HTTPException:
            state = "retry"
            with self.store.connect() as db:
                for article in fresh:
                    db.execute("UPDATE news_alert_deliveries SET attempts=attempts-1 WHERE subscription_id=? AND article_id=?", (subscription["id"], article["id"]))
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPConnectError, smtplib.SMTPRecipientsRefused,
                smtplib.SMTPSenderRefused, smtplib.SMTPDataError, ConnectionRefusedError) as exc:
            state = "retry"
            LOG.warning("Alert email rejected before acceptance (%s)", type(exc).__name__)
        except Exception as exc:
            state = "unknown"
            LOG.warning("Alert email delivery uncertain; not retried automatically (%s)", type(exc).__name__)
        with self.store.connect() as db:
            for article in fresh:
                db.execute("UPDATE news_alert_deliveries SET state=?,updated=? WHERE subscription_id=? AND article_id=?",
                           (state, time.time(), subscription["id"], article["id"]))

    async def tick(self):
        if not self.enabled:
            return
        async with self.tick_lock:
            now = time.time()
            with self.store.connect() as db:
                expired = [r[0] for r in db.execute("SELECT id FROM news_subscriptions WHERE confirmed=0 AND confirmation_at<?", (now - 86400,))]
                for ident in expired:
                    db.execute("DELETE FROM news_subscriptions WHERE id=?", (ident,))
                db.execute("DELETE FROM news_alert_deliveries WHERE updated<?", (now - 30 * 86400,))
                db.execute("DELETE FROM budgets WHERE day < date('now', '-30 days')")
                rows = [dict(r) for r in db.execute("SELECT * FROM news_subscriptions WHERE confirmed=1")]
            groups = {}
            for row in rows:
                groups.setdefault(row["symbol"], []).append(row)
            # Assets that could not be queried get priority when quotas become available.
            ordered = sorted(groups, key=lambda symbol: (self.store.cached("alerts:last_success:" + symbol, 31 * 86400) or {"at": 0})["at"])
            for symbol in ordered:
                if self.stop.is_set():
                    return
                subscribers = groups[symbol]
                if all(now - row["last_checked"] < self.cfg.alert_interval_hours * 3600 for row in subscribers):
                    continue
                LOG.info("Checking news alerts for %s", symbol)
                with self.store.connect() as db:
                    db.execute("UPDATE news_subscriptions SET last_checked=? WHERE symbol=? AND confirmed=1", (now, symbol))
                try:
                    report = await self.news.report(ASSETS[symbol])
                except NoProviders:
                    LOG.info("News sources unavailable for %s; alerts deferred", symbol)
                    continue
                self.store.cache("alerts:last_success:" + symbol, {"at": time.time()})
                if not report["best_article"]:
                    continue
                for subscription in subscribers:
                    if self.stop.is_set():
                        return
                    await self.notify(subscription, report)

    async def run(self):
        LOG.info("Email alerts enabled: checking subscribed assets every %g minutes", self.cfg.alert_interval_hours * 60)
        while not self.stop.is_set():
            try:
                await self.tick()
            except Exception as exc:
                LOG.error("Alert check failed (%s); worker will retry", type(exc).__name__)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=30)
            except TimeoutError:
                pass

    def page(self, action, language):
        language = language if language in COPY else "en"
        copy = COPY[language]
        nonce = secrets.token_urlsafe(18)
        payload = json.dumps({"action": action, "ok": copy["done_" + action].format(minutes=format(self.cfg.alert_interval_hours * 60, "g")), "error": copy["error"]})
        title = html.escape(copy[action])
        content = f'''<!doctype html><html lang="{language}"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><title>{title} · Trading News</title>
<style nonce="{nonce}">body{{font:16px/1.6 system-ui;background:#f4f7fc;color:#123965;max-width:580px;margin:12vh auto;padding:24px}}button{{background:#175aa2;color:white;padding:14px 22px;border:0;border-radius:8px;font:inherit;cursor:pointer}}</style>
<h1>{title}</h1><p id="message" role="status">{html.escape(copy['action'])}</p><button id="action">{title}</button>
<script nonce="{nonce}">const data={payload};const token=location.hash.slice(1);history.replaceState(null,'',location.pathname+location.search);document.getElementById('action').onclick=async function(){{this.disabled=true;try{{const r=await fetch('/api/v1/alerts/'+data.action,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token}})}});if(!r.ok)throw Error();document.getElementById('message').textContent=data.ok;this.hidden=true;}}catch{{document.getElementById('message').textContent=data.error;this.disabled=false;}}}};</script></html>'''
        return HTMLResponse(content, headers={"Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
