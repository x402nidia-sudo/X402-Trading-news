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
from datetime import datetime, timezone
from urllib.parse import urlencode
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from .catalog import ASSETS
from .service import NoProviders
from .insights import build_insight
from .ranking import parse_date

LOG = logging.getLogger("uvicorn.error")
COPY = {
    "en": {
        "confirm": "Confirm news alerts",
        "confirm_body": "Confirm that you want email alerts when new relevant news is available for {symbol}. If you did not request this, ignore this email. This link expires in 24 hours.",
        "news": "New relevant news for {symbol}",
        "intro": "New relevant stories are available. Open Trading News to see availability and buy the complete report if you wish. This alert is free and does not make a purchase.",
        "open": "Open Trading News",
        "unsubscribe": "Unsubscribe",
        "done_confirm": "Subscription confirmed. We will check for new relevant news every {minutes} minutes, subject to source availability. You can close this page.",
        "done_unsubscribe": "You have been unsubscribed from these alerts.",
        "error": "The link is invalid, expired or temporarily unavailable. Try again or request a new email on the website.",
        "privacy": "Your email is used only for these news alerts. You can unsubscribe at any time.",
        "action": "Select the button to continue.",
        "all": "all coins",
        "importance_note": "The icon shows the highest importance among newly detected stories for each coin.",
        "high": "🔴 Very important",
        "medium": "🟠 Important",
        "low": "🟡 Less important",
        "unknown": "Importance unavailable",
        "buy": "Buy the {symbol} report",
        "test": "Send test email",
        "test_subject": "[TEST] Trading News email alerts",
        "test_intro": "This is a test email with sample importance levels. It does not announce real news, buy a report or mark any news as notified.",
        "test_sending": "Sending test email…",
        "test_sent": "Test email sent. Check your inbox and spam folder.",
        "test_limit": "The daily test email limit has been reached. Try again tomorrow."
    },
    "es": {
        "confirm": "Confirmar avisos de noticias",
        "confirm_body": "Confirma que quieres recibir avisos cuando haya noticias nuevas relevantes para {symbol}. Si no lo has solicitado, ignora este correo. El enlace caduca en 24 horas.",
        "news": "Nuevas noticias relevantes de {symbol}",
        "intro": "Hay nuevas noticias relevantes. Abre Trading News para consultar la disponibilidad y comprar el informe completo si lo deseas. Este aviso es gratuito y no realiza ninguna compra.",
        "open": "Abrir Trading News",
        "unsubscribe": "Dar de baja",
        "done_confirm": "Suscripción confirmada. Buscaremos nuevas noticias relevantes cada {minutes} minutos, según la disponibilidad de las fuentes. Puedes cerrar esta página.",
        "done_unsubscribe": "Se ha cancelado tu suscripción a estos avisos.",
        "error": "El enlace no es válido, ha caducado o el servicio no está disponible. Inténtalo de nuevo o solicita otro correo en la web.",
        "privacy": "Tu email se utiliza solo para estos avisos de noticias. Puedes darte de baja cuando quieras.",
        "action": "Pulsa el botón para continuar.",
        "all": "todas las monedas",
        "importance_note": "El icono indica la mayor importancia de las noticias nuevas detectadas para cada moneda.",
        "high": "🔴 Muy importante",
        "medium": "🟠 Importante",
        "low": "🟡 Menos importante",
        "unknown": "Importancia no disponible",
        "buy": "Comprar el informe de {symbol}",
        "test": "Enviar correo de prueba",
        "test_subject": "[PRUEBA] Avisos de Trading News",
        "test_intro": "Este es un correo de prueba con niveles de importancia de ejemplo. No anuncia noticias reales, no compra ningún informe ni marca noticias como notificadas.",
        "test_sending": "Enviando correo de prueba…",
        "test_sent": "Correo de prueba enviado. Revisa tu bandeja de entrada y spam.",
        "test_limit": "Has alcanzado el límite diario de correos de prueba. Inténtalo mañana."
    },
    "fr": {
        "confirm": "Confirmer les alertes",
        "confirm_body": "Confirmez que vous souhaitez recevoir des alertes pour les nouvelles actualités pertinentes de {symbol}. Si vous ne les avez pas demandées, ignorez cet e-mail. Le lien expire dans 24 heures.",
        "news": "Nouvelles actualités pertinentes : {symbol}",
        "intro": "De nouvelles actualités sont disponibles. Ouvrez Trading News pour consulter la disponibilité et acheter le rapport complet si vous le souhaitez. Cette alerte est gratuite et ne déclenche aucun achat.",
        "open": "Ouvrir Trading News",
        "unsubscribe": "Se désabonner",
        "done_confirm": "Abonnement confirmé. Nous rechercherons les nouvelles actualités toutes les {minutes} minutes, selon la disponibilité des sources. Vous pouvez fermer cette page.",
        "done_unsubscribe": "Vous êtes désabonné de ces alertes.",
        "error": "Le lien est invalide, expiré ou temporairement indisponible. Réessayez ou demandez un nouvel e-mail sur le site.",
        "privacy": "Votre e-mail sert uniquement à ces alertes. Vous pouvez vous désabonner à tout moment.",
        "action": "Cliquez sur le bouton pour continuer.",
        "all": "toutes les monnaies",
        "importance_note": "L’icône indique l’importance maximale des nouvelles actualités détectées pour chaque monnaie.",
        "high": "🔴 Très importante",
        "medium": "🟠 Importante",
        "low": "🟡 Moins importante",
        "unknown": "Importance indisponible",
        "buy": "Acheter le rapport {symbol}",
        "test": "Envoyer un e-mail de test",
        "test_subject": "[TEST] Alertes Trading News",
        "test_intro": "Ceci est un e-mail de test avec des niveaux d’importance fictifs. Il n’annonce aucune actualité réelle, n’achète aucun rapport et ne marque aucune actualité comme notifiée.",
        "test_sending": "Envoi de l’e-mail de test…",
        "test_sent": "E-mail de test envoyé. Vérifiez votre boîte de réception et vos spams.",
        "test_limit": "La limite quotidienne des e-mails de test a été atteinte. Réessayez demain."
    },
    "de": {
        "confirm": "Nachrichtenbenachrichtigungen bestätigen",
        "confirm_body": "Bestätigen Sie E-Mail-Benachrichtigungen über neue relevante Nachrichten zu {symbol}. Falls Sie diese nicht angefordert haben, ignorieren Sie diese E-Mail. Der Link läuft nach 24 Stunden ab.",
        "news": "Neue relevante Nachrichten zu {symbol}",
        "intro": "Neue relevante Nachrichten sind verfügbar. Öffnen Sie Trading News, um die Verfügbarkeit zu prüfen und bei Bedarf den vollständigen Bericht zu kaufen. Diese Benachrichtigung ist kostenlos und löst keinen Kauf aus.",
        "open": "Trading News öffnen",
        "unsubscribe": "Abmelden",
        "done_confirm": "Abonnement bestätigt. Wir prüfen alle {minutes} Minuten auf neue relevante Nachrichten, abhängig von der Verfügbarkeit der Quellen. Sie können diese Seite schließen.",
        "done_unsubscribe": "Sie wurden von diesen Benachrichtigungen abgemeldet.",
        "error": "Der Link ist ungültig, abgelaufen oder vorübergehend nicht verfügbar. Versuchen Sie es erneut oder fordern Sie auf der Website eine neue E-Mail an.",
        "privacy": "Ihre E-Mail wird nur für diese Benachrichtigungen verwendet. Sie können sich jederzeit abmelden.",
        "action": "Klicken Sie zum Fortfahren auf die Schaltfläche.",
        "all": "alle Kryptowährungen",
        "importance_note": "Das Symbol zeigt die höchste Bedeutung der neu erkannten Nachrichten je Kryptowährung.",
        "high": "🔴 Sehr wichtig",
        "medium": "🟠 Wichtig",
        "low": "🟡 Weniger wichtig",
        "unknown": "Bedeutung nicht verfügbar",
        "buy": "Bericht zu {symbol} kaufen",
        "test": "Test-E-Mail senden",
        "test_subject": "[TEST] Trading News Benachrichtigungen",
        "test_intro": "Dies ist eine Test-E-Mail mit beispielhaften Wichtigkeitsstufen. Sie meldet keine echten Nachrichten, kauft keinen Bericht und markiert keine Nachrichten als versendet.",
        "test_sending": "Test-E-Mail wird gesendet…",
        "test_sent": "Test-E-Mail gesendet. Prüfen Sie Ihren Posteingang und Spamordner.",
        "test_limit": "Das Tageslimit für Test-E-Mails wurde erreicht. Versuchen Sie es morgen erneut."
    }
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
        if (symbol not in ASSETS and symbol != "ALL") or language not in COPY:
            raise HTTPException(422, "INVALID_SUBSCRIPTION")
        now = time.time()
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM news_subscriptions WHERE email=? AND symbol='ALL' AND confirmed=1", (email,)).fetchone():
                return {"status": "confirmation_requested"}
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
            if db.execute("SELECT 1 FROM news_subscriptions WHERE email=? AND symbol='ALL' AND confirmed=1", (email,)).fetchone():
                return {"status": "confirmation_requested"}
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
        scope = copy["all"] if symbol == "ALL" else symbol
        body = copy["confirm_body"].format(symbol=scope) + "\n\n" + self.url("confirm", token, language) + "\n\n" + copy["privacy"]
        try:
            await self.send(email, copy["confirm"] + " · " + scope, body)
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
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM news_subscriptions WHERE confirm_hash=?", (digest(token),)).fetchone()
            if not row or (not row["confirmed"] and time.time() - row["confirmation_at"] > 86400):
                raise HTTPException(400, "INVALID_ALERT_LINK")
            if row["symbol"] == "ALL":
                # A verified ALL subscription replaces individual subscriptions.
                # Carry over delivery history so widening the scope doesn't repeat alerts.
                others = db.execute("SELECT id,symbol FROM news_subscriptions WHERE email=? AND id!=?", (row["email"], row["id"])).fetchall()
                for other in others:
                    for old in db.execute("SELECT * FROM news_alert_deliveries WHERE subscription_id=?", (other["id"],)).fetchall():
                        key = other["symbol"] + ":" + old["article_id"]
                        db.execute("INSERT OR IGNORE INTO news_alert_deliveries VALUES (?,?,?,?,?)",
                                   (row["id"], key, old["state"], old["updated"], old["attempts"]))
                    db.execute("DELETE FROM news_alert_deliveries WHERE subscription_id=?", (other["id"],))
                    db.execute("DELETE FROM news_subscriptions WHERE id=?", (other["id"],))
            db.execute("UPDATE news_subscriptions SET confirmed=1 WHERE id=?", (row["id"],))
        return {"status": "confirmed"}

    def unsubscribe(self, token):
        with self.store.connect() as db:
            row = db.execute("SELECT id FROM news_subscriptions WHERE unsubscribe_token=?", (token,)).fetchone()
            if row:
                db.execute("DELETE FROM news_alert_deliveries WHERE subscription_id=?", (row["id"],))
                db.execute("DELETE FROM news_subscriptions WHERE id=?", (row["id"],))
        return {"status": "unsubscribed"}

    def mail_content(self, subscription, levels, test=False):
        copy = COPY[subscription["language"]]
        symbols = sorted(levels)
        lines = [copy["test_intro"] if test else copy["intro"], "", copy["importance_note"], ""]
        for symbol in symbols:
            web = self.cfg.web_url + "/?" + urlencode({"asset": symbol, "lang": subscription["language"]})
            lines.extend([symbol + " · " + ASSETS[symbol]["name"] + " — " + copy[levels[symbol]],
                          copy["buy"].format(symbol=symbol) + ": " + web, ""])
        lines.extend([copy["privacy"], copy["unsubscribe"] + ": " + self.url("unsubscribe", subscription["unsubscribe_token"], subscription["language"])])
        subject = copy["test_subject"] if test else copy["news"].format(symbol=", ".join(symbols))
        return subject, "\n".join(lines)

    async def test_email(self, token):
        if not self.enabled:
            raise HTTPException(503, "EMAIL_NOT_CONFIGURED")
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM news_subscriptions WHERE confirm_hash=? AND confirmed=1", (digest(token),)).fetchone()
        if not row:
            raise HTTPException(400, "INVALID_ALERT_LINK")
        subscription = dict(row)
        if not self.store.budget("alert_tests:" + digest(subscription["email"]), 3):
            raise HTTPException(429, "EMAIL_LIMIT")
        levels = {"BTC": "high", "ETH": "medium", "ALGO": "low"} if subscription["symbol"] == "ALL" else {subscription["symbol"]: "high"}
        subject, body = self.mail_content(subscription, levels, test=True)
        try:
            await self.send(subscription["email"], subject, body)
        except HTTPException:
            raise
        except Exception as exc:
            LOG.warning("Test email failed (%s)", type(exc).__name__)
            raise HTTPException(503, "EMAIL_UNAVAILABLE") from None
        return {"status": "test_sent"}

    async def notify(self, subscription, reports):
        # Claim before SMTP. An ambiguous network interruption is never auto-retried:
        # SMTP cannot guarantee exactly-once delivery across a process crash.
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM news_subscriptions WHERE id=? AND confirmed=1", (subscription["id"],)).fetchone():
                return
            seen = {r["article_id"]: r for r in db.execute("SELECT * FROM news_alert_deliveries WHERE subscription_id=?", (subscription["id"],))}
            fresh = []
            instant = datetime.now(timezone.utc)
            today = instant.date()
            for symbol, report in reports.items():
                if symbol not in ASSETS or subscription["symbol"] not in {symbol, "ALL"}:
                    continue
                for article in report["articles"]:
                    published = parse_date(article.get("published_at"))
                    if published is None or published.date() != today or published > instant:
                        continue
                    key = symbol + ":" + article["id"] if subscription["symbol"] == "ALL" else article["id"]
                    if key not in seen or (seen[key]["state"] == "retry" and seen[key]["attempts"] < 3):
                        fresh.append((symbol, key, article))
            if not fresh:
                return
            for symbol, key, article in fresh:
                db.execute("""INSERT INTO news_alert_deliveries VALUES (?,?,'sending',?,1)
                    ON CONFLICT(subscription_id,article_id) DO UPDATE SET state='sending',updated=excluded.updated,attempts=attempts+1""",
                    (subscription["id"], key, now))
        levels = {}
        priority = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
        for symbol, key, article in fresh:
            insight = article.get("insight") or build_insight(article, ASSETS[symbol])
            level = insight.get("importance", {}).get("level", "unknown")
            if level not in priority:
                level = "unknown"
            if symbol not in levels or priority[level] > priority[levels[symbol]]:
                levels[symbol] = level
        # Only coin names, importance and purchase links enter the email template.
        subject, body = self.mail_content(subscription, levels)
        state = "sent"
        try:
            await self.send(subscription["email"], subject, body)
        except HTTPException:
            state = "retry"
            with self.store.connect() as db:
                for symbol, key, article in fresh:
                    db.execute("UPDATE news_alert_deliveries SET attempts=attempts-1 WHERE subscription_id=? AND article_id=?", (subscription["id"], key))
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPConnectError, smtplib.SMTPRecipientsRefused,
                smtplib.SMTPSenderRefused, smtplib.SMTPDataError, ConnectionRefusedError) as exc:
            state = "retry"
            LOG.warning("Alert email rejected before acceptance (%s)", type(exc).__name__)
        except Exception as exc:
            state = "unknown"
            LOG.warning("Alert email delivery uncertain; not retried automatically (%s)", type(exc).__name__)
        with self.store.connect() as db:
            for symbol, key, article in fresh:
                db.execute("UPDATE news_alert_deliveries SET state=?,updated=? WHERE subscription_id=? AND article_id=?",
                           (state, time.time(), subscription["id"], key))

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
            due = [row for row in rows if now - row["last_checked"] >= self.cfg.alert_interval_hours * 3600]
            symbols = set()
            for row in due:
                symbols.update(ASSETS if row["symbol"] == "ALL" else [row["symbol"]])
            with self.store.connect() as db:
                db.executemany("UPDATE news_subscriptions SET last_checked=? WHERE id=? AND confirmed=1",
                               [(now, row["id"]) for row in due])
            # Assets that could not be queried get priority when quotas become available.
            ordered = sorted(symbols, key=lambda symbol: ((self.store.cached("alerts:last_success:" + symbol, 31 * 86400) or {"at": 0})["at"], symbol))
            reports = {}
            for symbol in ordered:
                if self.stop.is_set():
                    return
                LOG.info("Checking news alerts for %s", symbol)
                try:
                    report = await self.news.report(ASSETS[symbol])
                except NoProviders:
                    LOG.info("News sources unavailable for %s; alerts deferred", symbol)
                    continue
                self.store.cache("alerts:last_success:" + symbol, {"at": time.time()})
                if not report["best_article"]:
                    continue
                reports[symbol] = report
            for subscription in due:
                if self.stop.is_set():
                    return
                await self.notify(subscription, reports)

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
        payload = json.dumps({"action": action, "ok": copy["done_" + action].format(minutes=format(self.cfg.alert_interval_hours * 60, "g")), "error": copy["error"],
                              "test_sending": copy["test_sending"], "test_sent": copy["test_sent"], "test_limit": copy["test_limit"]})
        title = html.escape(copy[action])
        content = f'''<!doctype html><html lang="{language}"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><title>{title} · Trading News</title>
<style nonce="{nonce}">body{{font:16px/1.6 system-ui;background:#f4f7fc;color:#123965;max-width:580px;margin:12vh auto;padding:24px}}button{{background:#175aa2;color:white;padding:14px 22px;border:0;border-radius:8px;font:inherit;cursor:pointer}}</style>
<h1>{title}</h1><p id="message" role="status">{html.escape(copy['action'])}</p><button id="action">{title}</button>
<button id="test" hidden>{html.escape(copy['test'])}</button><p id="test-result" role="status"></p>
<script nonce="{nonce}">const data={payload};const token=location.hash.slice(1);history.replaceState(null,'',location.pathname+location.search);document.getElementById('action').onclick=async function(){{this.disabled=true;try{{const r=await fetch('/api/v1/alerts/'+data.action,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token}})}});if(!r.ok)throw Error();document.getElementById('message').textContent=data.ok;this.hidden=true;document.getElementById('test').hidden=data.action!=='confirm';}}catch{{document.getElementById('message').textContent=data.error;this.disabled=false;}}}};
document.getElementById('test').onclick=async function(){{this.disabled=true;const result=document.getElementById('test-result');result.textContent=data.test_sending;try{{const r=await fetch('/api/v1/alerts/test',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token}})}});if(r.status===429){{result.textContent=data.test_limit;return;}}if(!r.ok)throw Error();result.textContent=data.test_sent;}}catch{{result.textContent=data.error;}}finally{{this.disabled=false;}}}};</script></html>'''
        return HTMLResponse(content, headers={"Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
