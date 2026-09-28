"""Conservative, explainable news impact rules and an escaped HTML article view.

Signals describe reported events in the available headline/excerpt. They are not
price forecasts; uncertainty, conflicting evidence and weak attribution yield HOLD.
"""
from datetime import datetime, timezone
import html
import re
from urllib.parse import urlencode
from .catalog import ASSETS
from .ranking import clean_text, contains, parse_date, canonical_url

COPY = {
 "en": {"high":"Very important", "medium":"Important", "low":"Less important", "importance":"News importance", "signal":"Indicative signal", "summary":"Key points", "why":"Why this signal", "source":"Open original source", "published":"Published", "analyzed":"Analyzed", "back":"Back to Trading News", "scope":"Rule-based interpretation of the headline and available excerpt, without OpenAI. The full article, prices and charts were not analyzed. This is not a personalized investment recommendation.", "historical":"Historical news: the signal refers to the publication, not to current market conditions.", "summary_scope":"Extracted from the text supplied by the news provider; this is not a summary of the full source article.", "no_excerpt":"The provider supplied no usable excerpt. Only the headline is available.", "positive":"The asset-specific text reports a favorable adoption, approval or operational event. The rule assigns BUY as a news-impact indicator, without forecasting returns.", "negative":"The asset-specific text reports an adverse security, regulatory or operational event. The rule assigns SELL as a news-impact indicator, without forecasting returns.", "mixed":"Positive and negative event patterns coexist. The rules cannot establish a clear direction, so the signal is HOLD.", "uncertain":"The text contains uncertainty, speculation, a question or a negation. The signal is HOLD.", "indirect":"The headline does not unambiguously attribute the event to the selected asset. The signal is HOLD to avoid attributing another asset's event to it.", "insufficient":"The available text does not provide enough explicit directional evidence. The signal is HOLD.", "high_reason":"The text concerns security, restrictions, an ETF decision or a major network disruption.", "medium_reason":"The text concerns adoption, a protocol development, macro conditions or an uncertain high-impact event.", "low_reason":"General market commentary or insufficient evidence of a major event.", "unavailable":"This news card is unavailable or has expired.", "paywall":"Today's news is available only in a purchased report. Open your report to view this card.", "title":"News card", "evidence":"Text supporting the rule"},
 "es": {"high":"Muy importante", "medium":"Importante", "low":"Menos importante", "importance":"Importancia de la noticia", "signal":"Señal orientativa", "summary":"Puntos principales", "why":"Por qué esta señal", "source":"Abrir fuente original", "published":"Publicación", "analyzed":"Análisis", "back":"Volver a Trading News", "scope":"Interpretación mediante reglas del titular y el extracto disponible, sin OpenAI. No se han analizado el artículo completo, precios ni gráficos. No es una recomendación de inversión personalizada.", "historical":"Noticia histórica: la señal se refiere a la publicación, no a las condiciones actuales del mercado.", "summary_scope":"Extraído del texto facilitado por el proveedor; no es un resumen del artículo completo de la fuente.", "no_excerpt":"El proveedor no facilitó un extracto útil. Solo está disponible el titular.", "positive":"El texto referido al activo describe un evento favorable de adopción, aprobación o funcionamiento. La regla asigna BUY como indicador del impacto de la noticia, sin predecir rentabilidad.", "negative":"El texto referido al activo describe un evento adverso de seguridad, regulación o funcionamiento. La regla asigna SELL como indicador del impacto de la noticia, sin predecir rentabilidad.", "mixed":"Coexisten patrones de eventos positivos y negativos. Las reglas no establecen una dirección clara y asignan HOLD.", "uncertain":"El texto contiene incertidumbre, especulación, una pregunta o una negación. La señal es HOLD.", "indirect":"El titular no atribuye inequívocamente el evento al activo seleccionado. La señal es HOLD para evitar atribuirle eventos de otra moneda.", "insufficient":"El texto disponible no aporta suficientes evidencias explícitas de dirección. La señal es HOLD.", "high_reason":"El texto trata sobre seguridad, restricciones, una decisión sobre ETF o una interrupción importante de la red.", "medium_reason":"El texto trata sobre adopción, cambios del protocolo, condiciones macro o un evento importante todavía incierto.", "low_reason":"Comentario general del mercado o evidencia insuficiente de un evento importante.", "unavailable":"Esta ficha no está disponible o ha caducado.", "paywall":"Las noticias de hoy solo están disponibles en un informe comprado. Abre tu informe para consultar esta ficha.", "title":"Ficha de noticia", "evidence":"Texto que fundamenta la regla"},
 "fr": {"high":"Très importante", "medium":"Importante", "low":"Moins importante", "importance":"Importance de l’actualité", "signal":"Signal indicatif", "summary":"Points essentiels", "why":"Pourquoi ce signal", "source":"Ouvrir la source originale", "published":"Publication", "analyzed":"Analyse", "back":"Retour à Trading News", "scope":"Interprétation par règles du titre et de l’extrait disponible, sans OpenAI. L’article complet, les prix et les graphiques ne sont pas analysés. Ce n’est pas un conseil en investissement personnalisé.", "historical":"Actualité historique : le signal concerne la publication, pas les conditions actuelles du marché.", "summary_scope":"Extrait du texte fourni par le service d’actualités ; ce n’est pas un résumé de l’article complet.", "no_excerpt":"Le fournisseur n’a transmis aucun extrait exploitable. Seul le titre est disponible.", "positive":"Le texte lié à l’actif décrit un événement favorable d’adoption, d’approbation ou de fonctionnement. La règle attribue BUY comme indicateur d’impact, sans prédire de rendement.", "negative":"Le texte lié à l’actif décrit un événement défavorable de sécurité, de réglementation ou de fonctionnement. La règle attribue SELL comme indicateur d’impact, sans prédire de rendement.", "mixed":"Des événements positifs et négatifs coexistent. Les règles n’établissent pas de direction claire : HOLD.", "uncertain":"Le texte contient de l’incertitude, une spéculation, une question ou une négation : HOLD.", "indirect":"Le titre n’attribue pas sans ambiguïté l’événement à l’actif sélectionné. HOLD évite de lui attribuer un événement concernant un autre actif.", "insufficient":"Le texte disponible ne contient pas assez d’éléments directionnels explicites : HOLD.", "high_reason":"Le texte concerne la sécurité, des restrictions, une décision sur un ETF ou une interruption majeure du réseau.", "medium_reason":"Le texte concerne l’adoption, le protocole, la macroéconomie ou un événement majeur encore incertain.", "low_reason":"Commentaire général ou preuves insuffisantes d’un événement majeur.", "unavailable":"Cette fiche est indisponible ou a expiré.", "paywall":"Les actualités du jour sont réservées aux rapports achetés. Ouvrez votre rapport pour consulter cette fiche.", "title":"Fiche d’actualité", "evidence":"Texte justifiant la règle"},
 "de": {"high":"Sehr wichtig", "medium":"Wichtig", "low":"Weniger wichtig", "importance":"Bedeutung der Nachricht", "signal":"Orientierendes Signal", "summary":"Kernaussagen", "why":"Begründung des Signals", "source":"Originalquelle öffnen", "published":"Veröffentlicht", "analyzed":"Analysiert", "back":"Zurück zu Trading News", "scope":"Regelbasierte Auswertung der Überschrift und des verfügbaren Auszugs, ohne OpenAI. Der vollständige Artikel, Preise und Charts wurden nicht analysiert. Keine persönliche Anlageempfehlung.", "historical":"Historische Nachricht: Das Signal bezieht sich auf die Veröffentlichung, nicht auf die aktuelle Marktlage.", "summary_scope":"Aus dem vom Nachrichtendienst gelieferten Text extrahiert; keine Zusammenfassung des vollständigen Artikels.", "no_excerpt":"Der Anbieter lieferte keinen verwendbaren Auszug. Nur die Überschrift ist verfügbar.", "positive":"Der Text zum Asset beschreibt ein günstiges Ereignis bei Nutzung, Zulassung oder Betrieb. Die Regel vergibt BUY als Nachrichtenindikator, ohne Renditen vorherzusagen.", "negative":"Der Text zum Asset beschreibt ein nachteiliges Ereignis bei Sicherheit, Regulierung oder Betrieb. Die Regel vergibt SELL als Nachrichtenindikator, ohne Renditen vorherzusagen.", "mixed":"Positive und negative Ereignisse treten gemeinsam auf. Keine eindeutige Richtung: HOLD.", "uncertain":"Der Text enthält Unsicherheit, Spekulation, eine Frage oder Verneinung: HOLD.", "indirect":"Die Überschrift ordnet das Ereignis dem ausgewählten Asset nicht eindeutig zu. HOLD verhindert eine falsche Zuordnung fremder Ereignisse.", "insufficient":"Der Text enthält zu wenige eindeutige Hinweise auf eine Richtung: HOLD.", "high_reason":"Der Text betrifft Sicherheit, Beschränkungen, eine ETF-Entscheidung oder einen größeren Netzwerkausfall.", "medium_reason":"Der Text betrifft Nutzung, Protokolländerungen, Makrobedingungen oder ein noch unsicheres wichtiges Ereignis.", "low_reason":"Allgemeiner Marktkommentar oder zu wenige Hinweise auf ein wichtiges Ereignis.", "unavailable":"Diese Nachricht ist nicht verfügbar oder abgelaufen.", "paywall":"Heutige Nachrichten sind nur in gekauften Berichten verfügbar. Öffnen Sie Ihren Bericht, um diese Nachricht zu lesen.", "title":"Nachrichtenübersicht", "evidence":"Textbeleg für die Regel"},
}

UNCERTAIN = re.compile(r"\b(may|might|could|rumou?r\w*|predict\w*|forecast\w*|speculat\w*|expected|plans? to|propos\w*|seeks?|pending|will|would|not|no|never|denies|denied reports|false|unconfirmed|unlikely|podr[ií]a\w*|rumor\w*|previ\w*|espera\w*|niega\w*|falso\w*|sin confirmar|patch\w*|fix(?:es|ed)?|resolv\w*|mitigat\w*|prevent\w*|avoids?|retrospective|recap|tutorial|guide|years ago|years since|parche\w*|solucion\w*|mitig\w*|previen\w*|gu[ií]a)\b|n['’]t\b|\?", re.I)
POSITIVE = re.compile(r"\b(partner(?:s|ed|ship)?|adopt(?:s|ed|ion)|integrat(?:es|ed|ion)|aprueba\w*|aprobado\w*|alianza\w*|adopci[oó]n|integra\w*)\b|\b(?:approv\w*).{0,45}\b(?:etf|fund|license)\b|\b(?:etf|fund|license).{0,45}\b(?:approv\w*)\b|\b(?:upgrade|mainnet).{0,35}\b(?:completed|deployed|activated|live)\b|\b(?:network|withdrawals|red).{0,25}\b(?:restored|resumed|restablecid\w*)\b", re.I)
NEGATIVE = re.compile(r"\b(hack(?:ed)?|exploit(?:ed)?|breach|stolen|outage|delist(?:s|ed|ing)?|bankrupt\w*|insolvenc\w*|hackeo|robo|vulnerabilidad\w*|quiebra)\b|\b(?:network|withdrawals|red|retiros).{0,30}\b(?:halt\w*|suspend\w*|stop\w*|interrump\w*)\b|\b(?:etf|license).{0,35}\b(?:reject\w*|denied|rechaz\w*)\b|\b(?:bans|banned|proh[ií]be\w*|prohibid\w*)\b", re.I)
MAJOR = re.compile(r"\b(hack\w*|exploit\w*|breach|stolen|vulnerab\w*|outage|halt\w*|ban(?:s|ned)?|lawsuit|bankrupt\w*|etf|hackeo|robo|quiebra|prohib\w*)\b", re.I)
MEDIUM = re.compile(r"\b(upgrade|mainnet|fork|adopt\w*|partner\w*|integrat\w*|launch\w*|regulat\w*|inflation|interest rate|federal reserve|adopci[oó]n|alianza\w*|actualiza\w*|inflaci[oó]n)\b", re.I)


def sentences(text):
    text = re.sub(r"\[\s*\+?\d+\s+chars?\s*\]", "", clean_text(text, 1200)).strip()
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) >= 12]


def mentions(text, asset):
    return any(contains(text, alias) for alias in asset["aliases"]) or bool(re.search(r"(?<!\w)\$?" + re.escape(asset["symbol"]) + r"(?!\w)", text))


def build_insight(article, asset):
    title = article["title"]
    excerpt = article.get("summary", "")
    available = sentences(excerpt)
    # Rank existing sentences only; never manufacture facts absent from the provider.
    candidates = sorted(enumerate(available), key=lambda pair: (-(3 * mentions(pair[1], asset) + 2 * bool(MAJOR.search(pair[1]) or MEDIUM.search(pair[1])) + bool(re.search(r"\d", pair[1]))), pair[0]))
    points, size = [], 0
    for _, sentence in candidates:
        if sentence.casefold() == title.casefold() or sentence in points:
            continue
        if size + len(sentence) <= 600:
            points.append(sentence)
            size += len(sentence)
        if len(points) == 2:
            break
    if not points and available:
        # Bounded extract with an explicit ellipsis when a provider supplied a long sentence.
        points = [available[0][:597].rsplit(" ", 1)[0] + "…" if len(available[0]) > 600 else available[0]]
    if not points:
        points = [title]
    other_assets = any(mentions(title, other) for other in ASSETS.values() if other["symbol"] != asset["symbol"])
    direct = mentions(title, asset) and not other_assets
    relevant = [title] if direct else []
    relevant += [s for s in available if mentions(s, asset)]
    text = " ".join(relevant)
    uncertain = bool(UNCERTAIN.search(title + " " + excerpt))
    positive, negative = bool(POSITIVE.search(text)), bool(NEGATIVE.search(text))
    if not direct:
        signal, reason = "HOLD", "indirect"
    elif uncertain:
        signal, reason = "HOLD", "uncertain"
    elif len(excerpt.strip()) < 20:
        signal, reason = "HOLD", "insufficient"
    elif positive and negative:
        signal, reason = "HOLD", "mixed"
    elif positive:
        signal, reason = "BUY", "positive"
    elif negative:
        signal, reason = "SELL", "negative"
    else:
        signal, reason = "HOLD", "insufficient"
    event_text = title + " " + excerpt
    level = "high" if MAJOR.search(event_text) else "medium" if MEDIUM.search(event_text) else "low"
    if uncertain and level == "high":
        level = "medium"
    if not direct or not available:
        level = "low" if not direct else "medium" if level == "high" else level
    return {"method":"rules-v1", "analyzed_at":datetime.now(timezone.utc).isoformat(),
            "importance":{"level":level,"color":{"high":"red","medium":"orange","low":"yellow"}[level],"reason":level+"_reason"},
            "recommendation":{"signal":signal,"reason":reason,"evidence":relevant[:3]},
            "key_points":points,"summary_basis":"provider_excerpt" if available else "headline_only"}


CSS = """
:root{color-scheme:light;font-family:Inter,Arial,sans-serif;color:#123965;background:#f4f7fc}*{box-sizing:border-box}body{max-width:1000px;margin:auto;padding:30px 22px;line-height:1.6}a{color:#175aa2}header{display:flex;justify-content:space-between;gap:20px;border-bottom:1px solid #dbe4ef;padding-bottom:18px}.brand{font-weight:800;text-decoration:none;font-size:24px}.eyebrow{font-size:12px;letter-spacing:2px;margin-top:32px;color:#586c83}.asset{font-size:22px;font-weight:700}h1{font-size:clamp(25px,4vw,40px);line-height:1.25;letter-spacing:-.8px}h2{font-size:20px}.meta,.fine{color:#586c83;font-size:13px}.meta{display:flex;gap:22px;flex-wrap:wrap}.status{display:flex;align-items:center;gap:28px;flex-wrap:wrap;background:#123965;color:white;padding:24px;border-radius:12px;margin:26px 0}.status small{display:block;font-size:12px;color:#c6e4ff}.signal{font-size:38px;font-weight:800}.importance{display:flex;align-items:center;gap:10px;font-weight:700}.dot{display:inline-block;width:16px;height:16px;border-radius:50%;box-shadow:0 0 0 4px #ffffff25}.high{background:#e53b36}.medium{background:#f08a18}.low{background:#e6c92f}.panel{background:white;border:1px solid #dbe4ef;padding:22px 26px;border-radius:12px;margin:20px 0}.panel li{margin:10px 0}blockquote{border-left:3px solid #90bce8;margin:18px 0;padding:8px 18px;color:#586c83}.original{display:inline-block;background:#175aa2;color:white;text-decoration:none;padding:12px 18px;border-radius:8px}.notice{background:#eaf2ff;border:1px solid #bed1ed;border-radius:8px;padding:14px 18px;font-size:13px}.scope{margin-top:30px;border-top:1px solid #dbe4ef;padding-top:18px}a:focus-visible{outline:3px solid #70a8e8;outline-offset:4px}@media(max-width:550px){header{flex-direction:column;gap:8px}.panel{padding:18px}.meta{gap:6px;flex-direction:column}.status{padding:20px;gap:20px}}
"""
CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def html_page(asset, language, web_url, article=None, error="unavailable"):
    language = language if language in COPY else "en"
    t = COPY[language]
    esc = lambda value: html.escape(str(value), quote=True)
    back = web_url + "/?" + urlencode({"asset":asset["symbol"],"lang":language})
    top = f'<header><a class="brand" href="{esc(back)}">tradingnews</a><a href="{esc(back)}">{esc(t["back"])}</a></header>'
    if not article:
        content = f'<h1>{esc(t["title"])}</h1><p class="notice">{esc(t[error])}</p>'
        title = t["title"]
    else:
        insight = article.get("insight") or build_insight(article, asset)
        level = insight["importance"]["level"]
        signal = insight["recommendation"]["signal"]
        published = parse_date(article["published_at"])
        analyzed = parse_date(insight["analyzed_at"])
        stamp = lambda dt: dt.strftime("%Y-%m-%d · %H:%M UTC") if dt else "—"
        historical = published and published.date() < datetime.now(timezone.utc).date()
        points = "".join('<li>'+esc(point)+'</li>' for point in insight["key_points"])
        evidence = "".join('<blockquote>'+esc(s)+'</blockquote>' for s in insight["recommendation"]["evidence"])
        reason = insight["recommendation"]["reason"]
        source_url = article["url"] if canonical_url(article.get("url")) else None
        source = f'<a class="original" href="{esc(source_url)}" target="_blank" rel="noopener noreferrer">{esc(t["source"])} ↗</a>' if source_url else ''
        title = article["title"]
        content = f'''<p class="eyebrow">{esc(t["title"])}</p><div class="asset">{esc(asset["name"])} / {esc(asset["symbol"])}</div><h1>{esc(title)}</h1>
<div class="meta"><span>{esc(t["published"])}: {esc(stamp(published))}</span><span>{esc(article["source"])}</span></div>
<div class="status"><div><small>{esc(t["signal"])}</small><strong class="signal">{signal}</strong></div><div><small>{esc(t["importance"])}</small><div class="importance"><span class="dot {level}" aria-hidden="true"></span>{esc(t[level])}</div></div></div>
{('<p class="notice">'+esc(t['historical'])+'</p>') if historical else ''}
<section class="panel"><h2>{esc(t["summary"])}</h2><ul>{points}</ul><p class="fine">{esc(t['no_excerpt'] if insight['summary_basis']=='headline_only' else t['summary_scope'])}</p></section>
<section class="panel"><h2>{esc(t["why"])}</h2><p>{esc(t[reason])}</p><h2>{esc(t["importance"])}</h2><p>{esc(t[insight['importance']['reason']])}</p><details><summary>{esc(t["evidence"])}</summary>{evidence}</details></section>
{source}<p class="fine">{esc(t["analyzed"])}: {esc(stamp(analyzed))}</p><p class="fine scope">{esc(t["scope"])}</p>'''
    return f'''<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="{esc(CSP)}"><title>{esc(title)} · Trading News</title><style>{CSS}</style></head><body>{top}<main>{content}</main></body></html>'''
