import os
import re
import json
import html
import statistics
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURAZIONE
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SEEN_FILE = "seen_items.json"
HISTORY_FILE = "market_history.json"
REPORT_STATE_FILE = "report_state.json"

ITALY_TZ = ZoneInfo("Europe/Rome")

TIMEOUT = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/128.0 Safari/537.36"
    )
}


# ============================================================
# QUERY DI RICERCA
# ============================================================

QUERIES = [
    '"LE1252"',
    '"Fossil LE1252"',
    '"Minecraft Fossil" "LE1252"',
    '"Minecraft x Fossil" "The End"',
]


# ============================================================
# FILE JSON
# ============================================================

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    temp_path = path + ".tmp"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_path, path)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False
        },
        timeout=TIMEOUT,
    )

    response.raise_for_status()


# ============================================================
# UTILITY
# ============================================================

def clean_text(text):

    return re.sub(
        r"\s+",
        " ",
        html.unescape(text or "")
    ).strip()


def normalize_url(url):

    if not url:
        return ""

    url = html.unescape(url).strip()

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return url

    return (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        + (f"?{parsed.query}" if parsed.query else "")
    )


# ============================================================
# RILEVANZA
# ============================================================

def is_relevant(title, description=""):

    text = clean_text(
        f"{title} {description}"
    ).lower()

    compact = text.replace(" ", "")

    # Identificatore esatto
    if "le1252" in compact:
        return True

    watch_words = [
        "fossil",
        "watch",
        "orologio",
        "cronografo",
        "chrono"
    ]

    minecraft_words = [
        "minecraft",
        "the end",
        "ender",
        "ender dragon"
    ]

    return (
        any(word in text for word in watch_words)
        and
        any(word in text for word in minecraft_words)
    )


# ============================================================
# PREZZI
# ============================================================

def parse_price(text):

    if not text:
        return None, None

    text = clean_text(text)

    patterns = [

        # Euro
        (
            r"(?:€|EUR)\s*"
            r"([0-9]{1,5}(?:[.,][0-9]{1,2})?)",
            "EUR"
        ),

        (
            r"([0-9]{1,5}(?:[.,][0-9]{1,2})?)\s*"
            r"(?:€|EUR)",
            "EUR"
        ),

        # Dollari
        (
            r"(?:\$|USD)\s*"
            r"([0-9]{1,5}(?:[.,][0-9]{1,2})?)",
            "USD"
        ),
    ]

    for pattern, currency in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if not match:
            continue

        raw = match.group(1)

        if "," in raw and "." in raw:
            raw = raw.replace(".", "")
            raw = raw.replace(",", ".")

        elif "," in raw:
            raw = raw.replace(",", ".")

        try:
            return float(raw), currency

        except ValueError:
            pass

    return None, None


# ============================================================
# AGGIUNTA RISULTATO
# ============================================================

def add_item(
    items,
    source,
    title,
    url,
    price_text="",
    description="",
    item_id=None,
    published=None,
    kind="listing"
):

    title = clean_text(title)
    description = clean_text(description)

    url = normalize_url(url)

    if not url:
        return

    if not title:
        return

    if not is_relevant(
        title,
        description
    ):
        return

    price, currency = parse_price(
        price_text
        or f"{title} {description}"
    )

    stable_id = item_id or url

    items.append({

        "id": stable_id,

        "source": source,

        "title": title,

        "url": url,

        "price": price,

        "currency": currency,

        "kind": kind,

        "published": published,
    })


# ============================================================
# RICERCA WEB
# ============================================================

def search_duckduckgo(
    query,
    max_results=10
):

    results = []

    url = (
        "https://html.duckduckgo.com/html/?q="
        + quote_plus(query)
    )

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT
        )

        response.raise_for_status()

    except requests.RequestException as error:

        print(
            f"[web] ricerca fallita: {error}"
        )

        return results

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    for link in soup.select(
        "a.result__a"
    )[:max_results]:

        title = clean_text(
            link.get_text(
                " ",
                strip=True
            )
        )

        href = link.get(
            "href",
            ""
        )

        parent = link.find_parent(
            "div",
            class_="result"
        )

        snippet = ""

        if parent:

            snippet_element = parent.select_one(
                ".result__snippet"
            )

            if snippet_element:

                snippet = clean_text(
                    snippet_element.get_text(
                        " ",
                        strip=True
                    )
                )

        add_item(
            results,
            "Web",
            title,
            href,
            description=snippet,
            kind="web"
        )

    return results


def search_web():

    items = []

    for query in QUERIES:

        searches = [

            query,

            f"site:ebay.com {query}",

            f"site:ebay.it {query}",

            f"site:vinted.it {query}",

            f"site:vinted.com {query}",

            f"site:reddit.com {query}",

            f"{query} watch forum",

        ]

        for search_query in searches:

            items.extend(
                search_duckduckgo(
                    search_query,
                    max_results=8
                )
            )

    return dedupe_items(items)


# ============================================================
# REDDIT
# ============================================================

def search_reddit():

    items = []

    queries = [
        "LE1252",
        '"Fossil" "Minecraft" watch'
    ]

    for query in queries:

        rss_url = (
            "https://www.reddit.com/search.rss"
            "?q="
            + quote_plus(query)
            + "&sort=new&t=month"
        )

        try:

            response = requests.get(
                rss_url,
                headers={
                    **HEADERS,
                    "Accept": "application/rss+xml"
                },
                timeout=TIMEOUT
            )

            response.raise_for_status()

            root = ET.fromstring(
                response.text
            )

        except Exception as error:

            print(
                f"[reddit] ricerca fallita: {error}"
            )

            continue

        namespace = {
            "atom":
            "http://www.w3.org/2005/Atom"
        }

        for entry in root.findall(
            "atom:entry",
            namespace
        ):

            title = entry.findtext(
                "atom:title",
                default="",
                namespaces=namespace
            )

            link_element = entry.find(
                "atom:link",
                namespace
            )

            link = ""

            if link_element is not None:

                link = link_element.get(
                    "href",
                    ""
                )

            summary = entry.findtext(
                "atom:content",
                default="",
                namespaces=namespace
            )

            published = entry.findtext(
                "atom:updated",
                default="",
                namespaces=namespace
            )

            if is_relevant(
                title,
                summary
            ):

                description = BeautifulSoup(
                    summary,
                    "html.parser"
                ).get_text(" ")

                add_item(
                    items,
                    "Reddit",
                    title,
                    link,
                    description=description,
                    published=published,
                    kind="discussion"
                )

    return dedupe_items(items)


# ============================================================
# EBAY API
# ============================================================

def ebay_access_token():

    client_id = os.environ.get(
        "EBAY_CLIENT_ID"
    )

    client_secret = os.environ.get(
        "EBAY_CLIENT_SECRET"
    )

    if not client_id or not client_secret:

        print(
            "[eBay] API non configurata."
        )

        return None

    try:

        response = requests.post(

            "https://api.ebay.com/"
            "identity/v1/oauth2/token",

            headers={
                "Content-Type":
                "application/x-www-form-urlencoded"
            },

            auth=requests.auth.HTTPBasicAuth(
                client_id,
                client_secret
            ),

            data={
                "grant_type":
                "client_credentials",

                "scope":
                "https://api.ebay.com/"
                "oauth/api_scope"
            },

            timeout=TIMEOUT
        )

        response.raise_for_status()

        return response.json()["access_token"]

    except Exception as error:

        print(
            f"[eBay] errore autenticazione: {error}"
        )

        return None


def search_ebay():

    token = ebay_access_token()

    if not token:

        print(
            "[eBay] API non disponibile. "
            "Verrà utilizzata la ricerca web."
        )

        return []

    items = []

    endpoint = (
        "https://api.ebay.com/"
        "buy/browse/v1/item_summary/search"
    )

    queries = [
        "LE1252",
        "Fossil Minecraft The End",
        "Minecraft Fossil watch"
    ]

    for query in queries:

        try:

            response = requests.get(

                endpoint,

                headers={
                    "Authorization":
                    f"Bearer {token}",

                    "X-EBAY-C-MARKETPLACE-ID":
                    "EBAY_US",

                    "Accept":
                    "application/json"
                },

                params={
                    "q": query,
                    "limit": 50
                },

                timeout=TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

        except Exception as error:

            print(
                f"[eBay] ricerca fallita "
                f"({query}): {error}"
            )

            continue

        for result in data.get(
            "itemSummaries",
            []
        ):

            title = result.get(
                "title",
                ""
            )

            url = (
                result.get("itemWebUrl")
                or
                result.get(
                    "itemAffiliateWebUrl",
                    ""
                )
            )

            item_id = result.get(
                "itemId"
            )

            price = result.get(
                "price",
                {}
            )

            price_text = (
                f"{price.get('currency', '')} "
                f"{price.get('value', '')}"
            )

            add_item(

                items,

                "eBay",

                title,

                url,

                price_text=price_text,

                description=result.get(
                    "shortDescription",
                    ""
                ),

                item_id=(
                    f"ebay:{item_id}"
                    if item_id
                    else None
                ),

                kind="listing"
            )

    return dedupe_items(items)


# ============================================================
# DEDUPLICAZIONE
# ============================================================

def dedupe_items(items):

    output = []

    seen = set()

    for item in items:

        key = (
            item["id"]
            or
            item["url"]
        )

        if key in seen:
            continue

        seen.add(key)

        output.append(item)

    return output


# ============================================================
# CLASSIFICAZIONE FONTE
# ============================================================

def classify_source(item):

    url = item["url"].lower()

    source = item["source"].lower()

    if (
        "ebay." in url
        or source == "ebay"
    ):
        return "eBay"

    if "vinted." in url:
        return "Vinted"

    if (
        "reddit.com" in url
        or source == "reddit"
    ):
        return "Reddit"

    return item["source"]


# ============================================================
# FORMATTAZIONE PREZZO
# ============================================================

def format_price(item):

    price = item.get(
        "price"
    )

    if price is None:

        return "Prezzo non rilevato"

    currency = item.get(
        "currency"
    ) or "?"

    symbols = {
        "EUR": "€",
        "USD": "$",
        "GBP": "£"
    }

    symbol = symbols.get(
        currency,
        currency + " "
    )

    return (
        f"{symbol}"
        f"{price:.2f}"
    )


# ============================================================
# NOTIFICA NUOVO RISULTATO
# ============================================================

def send_new_item(item):

    source = classify_source(
        item
    )

    if item["kind"] == "discussion":

        prefix = "📰 NUOVA DISCUSSIONE"

    elif source == "eBay":

        prefix = "🟢 NUOVA INSERZIONE"

    elif source == "Vinted":

        prefix = "🟢 NUOVO ANNUNCIO"

    else:

        prefix = "🔎 NUOVA SEGNALAZIONE"

    message = (

        f"{prefix}\n\n"

        f"{source}: "
        f"{item['title']}\n"

        f"Prezzo: "
        f"{format_price(item)}\n\n"

        f"{item['url']}"
    )

    send_telegram(
        message
    )


# ============================================================
# PREZZI EUR
# ============================================================

def collect_prices(items):

    prices = []

    for item in items:

        if (
            item.get("price") is not None
            and
            item.get("currency") == "EUR"
        ):

            prices.append(
                float(item["price"])
            )

    return prices


# ============================================================
# STORICO GIORNALIERO
# ============================================================

def save_daily_history(
    all_current_items,
    now
):

    history = load_json(
        HISTORY_FILE,
        {}
    )

    date_key = now.date().isoformat()

    grouped = {}

    for item in all_current_items:

        source = classify_source(
            item
        )

        grouped.setdefault(
            source,
            []
        )

        if (
            item.get("price") is not None
            and
            item.get("currency") == "EUR"
        ):

            grouped[source].append(
                item["price"]
            )

    snapshot = {}

    for source, prices in grouped.items():

        if prices:

            snapshot[source] = {

                "count":
                len(prices),

                "min":
                min(prices),

                "max":
                max(prices),

                "average":
                statistics.mean(prices)
            }

        else:

            snapshot[source] = {
                "count": 0
            }

    history[date_key] = snapshot

    # Mantiene gli ultimi 90 giorni.
    keys = sorted(
        history.keys()
    )

    for key in keys[:-90]:

        del history[key]

    save_json(
        HISTORY_FILE,
        history
    )

    return history


# ============================================================
# VARIAZIONE PREZZO
# ============================================================

def pct_change(
    current,
    previous
):

    if (
        previous in (None, 0)
        or
        current is None
    ):

        return None

    return (
        (current - previous)
        /
        previous
    ) * 100


# ============================================================
# REPORT GIORNALIERO
# ============================================================

def build_daily_report(
    all_items,
    new_items,
    now,
    history
):

    today = now.date().isoformat()

    yesterday = (
        now.date()
        .fromordinal(
            now.date().toordinal() - 1
        )
        .isoformat()
    )

    sources = {}

    for item in all_items:

        source = classify_source(
            item
        )

        sources.setdefault(
            source,
            []
        ).append(item)

    lines = [

        "📊 LE1252 MARKET REPORT "
        f"— {now.strftime('%d/%m/%Y')}",

        ""
    ]

    for source in [
        "eBay",
        "Vinted",
        "Reddit",
        "Web"
    ]:

        results = sources.get(
            source,
            []
        )

        if source == "Reddit":

            if results:

                lines.append(
                    f"Reddit: "
                    f"{len(results)} "
                    f"discussioni trovate"
                )

            else:

                lines.append(
                    "Reddit: nessuna "
                    "discussione rilevata"
                )

            continue

        prices = [

            item["price"]

            for item in results

            if (
                item.get("price")
                is not None
                and
                item.get("currency")
                == "EUR"
            )
        ]

        if prices:

            lines.append(

                f"{source}: "
                f"{len(results)} risultati | "

                f"min €{min(prices):.2f} | "

                f"media €"
                f"{statistics.mean(prices):.2f} | "

                f"max €{max(prices):.2f}"
            )

        else:

            lines.append(

                f"{source}: "
                f"{len(results)} risultati | "
                "prezzi non rilevati"
            )

    new_today = [

        item

        for item in new_items

        if classify_source(item)
        in (
            "eBay",
            "Vinted",
            "Web",
            "Reddit"
        )
    ]

    lines.append("")

    lines.append(
        f"🆕 Nuove segnalazioni oggi: "
        f"{len(new_today)}"
    )

    today_prices = collect_prices(
        all_items
    )

    yesterday_data = history.get(
        yesterday,
        {}
    )

    previous_averages = []

    for data in yesterday_data.values():

        if data.get("average") is not None:

            previous_averages.append(
                data["average"]
            )

    if (
        today_prices
        and
        previous_averages
    ):

        today_average = statistics.mean(
            today_prices
        )

        yesterday_average = statistics.mean(
            previous_averages
        )

        change = pct_change(
            today_average,
            yesterday_average
        )

        if change is not None:

            sign = (
                "+"
                if change >= 0
                else ""
            )

            lines.append(

                f"📈 Prezzo medio vs ieri: "
                f"{sign}{change:.1f}%"
            )

    else:

        lines.append(
            "📈 Prezzo medio vs ieri: "
            "dati storici insufficienti"
        )

    lines.append("")

    lines.append(
        "ℹ️ I prezzi sono prezzi richiesti, "
        "non necessariamente prezzi di vendita."
    )

    return "\n".join(lines)


# ============================================================
# REPORT GIORNALIERO
# ============================================================

def should_send_daily_report(now):

    # Il report viene inviato dopo le 18:00
    # ora italiana.

    if now.hour < 18:

        return False

    state = load_json(
        REPORT_STATE_FILE,
        {}
    )

    return (
        state.get("last_report_date")
        != now.date().isoformat()
    )


def mark_daily_report_sent(now):

    save_json(
        REPORT_STATE_FILE,
        {
            "last_report_date":
            now.date().isoformat()
        }
    )


# ============================================================
# MAIN
# ============================================================

def main():

    # --------------------------------------------------------
    # TEST MANUALE TELEGRAM
    # --------------------------------------------------------

    if (
        os.environ.get(
            "FORCE_NOTIFY",
            "false"
        ).lower()
        == "true"
    ):

        send_telegram(
            "🧪 Test manuale: "
            "LE1252 Market Radar "
            "funziona correttamente."
        )

        print(
            "Messaggio di test Telegram inviato."
        )

    # --------------------------------------------------------
    # ORA
    # --------------------------------------------------------

    now = (
        datetime.now(
            timezone.utc
        )
        .astimezone(
            ITALY_TZ
        )
    )

    print(
        f"🕐 Radar LE1252: "
        f"{now.isoformat()}"
    )

    # --------------------------------------------------------
    # CARICA ELEMENTI GIÀ VISTI
    # --------------------------------------------------------

    seen = load_json(
        SEEN_FILE,
        {}
    )

    if not isinstance(
        seen,
        dict
    ):

        seen = {}

    # --------------------------------------------------------
    # RICERCHE
    # --------------------------------------------------------

    current = []

    print(
        "🔎 Ricerca eBay..."
    )

    ebay_items = search_ebay()

    print(
        f"eBay API: "
        f"{len(ebay_items)} risultati"
    )

    print(
        "🔎 Ricerca web..."
    )

    web_items = search_web()

    print(
        f"Web: "
        f"{len(web_items)} risultati"
    )

    print(
        "🔎 Ricerca Reddit..."
    )

    reddit_items = search_reddit()

    print(
        f"Reddit: "
        f"{len(reddit_items)} risultati"
    )

    current.extend(
        ebay_items
    )

    current.extend(
        web_items
    )

    current.extend(
        reddit_items
    )

    current = dedupe_items(
        current
    )

    print(
        f"🔎 Risultati rilevanti totali: "
        f"{len(current)}"
    )

    # --------------------------------------------------------
    # TROVA NUOVI RISULTATI
    # --------------------------------------------------------

    new_items = []

    for item in current:

        key = (
            item["id"]
            or
            item["url"]
        )

        if key not in seen:

            new_items.append(
                item
            )

            seen[key] = {

                "first_seen":
                now.isoformat(),

                "source":
                classify_source(item),

                "title":
                item["title"],

                "url":
                item["url"],

                "price":
                item.get("price"),

                "currency":
                item.get("currency")
            }

    # --------------------------------------------------------
    # INVIA NOTIFICHE
    # --------------------------------------------------------

    for item in new_items:

        try:

            send_new_item(
                item
            )

            print(
                f"🔔 Notificato: "
                f"{item['title']}"
            )

        except Exception as error:

            print(
                f"❌ Errore Telegram: "
                f"{error}"
            )

    # --------------------------------------------------------
    # SALVA ELEMENTI VISTI
    # --------------------------------------------------------

    save_json(
        SEEN_FILE,
        seen
    )

    # --------------------------------------------------------
    # SALVA STORICO PREZZI
    # --------------------------------------------------------

    history = save_daily_history(
        current,
        now
    )

    # --------------------------------------------------------
    # REPORT GIORNALIERO
    # --------------------------------------------------------

    if should_send_daily_report(
        now
    ):

        try:

            report = build_daily_report(
                current,
                new_items,
                now,
                history
            )

            send_telegram(
                report
            )

            mark_daily_report_sent(
                now
            )

            print(
                "📊 Report giornaliero inviato."
            )

        except Exception as error:

            print(
                f"❌ Errore report Telegram: "
                f"{error}"
            )

    # --------------------------------------------------------
    # FINE
    # --------------------------------------------------------

    print(
        f"🆕 Nuovi elementi trovati: "
        f"{len(new_items)}"
    )


if __name__ == "__main__":

    main()
