import os
import re
import json
import html
import statistics
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

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

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/128.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
}


# ============================================================
# RICERCHE
# ============================================================

SEARCHES = [
    "LE1252",
    '"Fossil Minecraft" watch',
    '"Minecraft Fossil" watch',
    '"Minecraft x Fossil"',
    '"Fossil x Minecraft"',
    '"Fossil Minecraft" "The End"',
]


# ============================================================
# PAROLE DA ESCLUDERE
# ============================================================

EXCLUDED_BRANDS = [
    "louis vuitton",
    "gucci",
    "prada",
    "chanel",
    "hermes",
    "hermès",
    "rolex",
    "omega",
    "seiko",
    "casio",
    "citizen",
    "swatch",
    "timex",
    "tag heuer",
    "tissot",
    "cartier",
    "armani",
    "diesel",
    "versace",
    "bulgari",
    "breguet",
    "patek philippe",
    "audemars piguet",
]


# ============================================================
# FILE JSON
# ============================================================

def load_json(path, default):

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):

        return default


def save_json(path, data):

    temp_path = path + ".tmp"

    with open(
        temp_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp_path,
        path
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    response = requests.post(

        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage",

        data={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False
        },

        timeout=TIMEOUT
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

    url = html.unescape(
        url
    ).strip()

    parsed = urlparse(url)

    if parsed.scheme not in (
        "http",
        "https"
    ):
        return ""

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"{parsed.path}"
    )


# ============================================================
# FILTRO MINECRAFT × FOSSIL
# ============================================================

def is_relevant(
    title,
    description="",
    url=""
):

    text = clean_text(
        f"{title} {description} {url}"
    ).lower()

    compact = re.sub(
        r"[^a-z0-9]",
        "",
        text
    )

    # --------------------------------------------------------
    # ESCLUSIONE ALTRI BRAND
    # --------------------------------------------------------

    for brand in EXCLUDED_BRANDS:

        if brand in text:

            # LE1252 ha priorità assoluta
            if "le1252" not in compact:

                return False

    # --------------------------------------------------------
    # CODICE PRODOTTO
    # --------------------------------------------------------

    if "le1252" in compact:

        return True

    # --------------------------------------------------------
    # RIFERIMENTI ESPLICITI ALLA COLLEZIONE
    # --------------------------------------------------------

    strong_patterns = [

        r"minecraft\s*x\s*fossil",
        r"fossil\s*x\s*minecraft",

        r"minecraft\s+×\s+fossil",
        r"fossil\s+×\s+minecraft",

        r"minecraft\s+and\s+fossil",
        r"fossil\s+and\s+minecraft",

        r"minecraft\s+fossil\s+collection",
        r"fossil\s+minecraft\s+collection",

        r"minecraft\s+fossil\s+watch",
        r"fossil\s+minecraft\s+watch",

        r"minecraft\s+fossil\s+chrono",
        r"fossil\s+minecraft\s+chrono",

        r"minecraft\s+fossil\s+chronograph",
        r"fossil\s+minecraft\s+chronograph",

        r"minecraft\s+fossil\s+the\s+end",
        r"fossil\s+minecraft\s+the\s+end",

        r"minecraft\s+fossil\s+ender",
        r"fossil\s+minecraft\s+ender",
    ]

    for pattern in strong_patterns:

        if re.search(
            pattern,
            text,
            re.IGNORECASE
        ):

            return True

    # --------------------------------------------------------
    # COMBINAZIONE SPECIFICA
    # --------------------------------------------------------

    has_minecraft = (
        "minecraft" in text
    )

    has_fossil = (
        "fossil" in text
    )

    collection_terms = [

        "the end",
        "ender dragon",
        "ender",
        "minecraft watch",
        "minecraft chrono",
        "minecraft chronograph",
        "minecraft collection",
    ]

    has_collection_term = any(
        term in text
        for term in collection_terms
    )

    if (
        has_minecraft
        and
        has_fossil
        and
        has_collection_term
    ):

        return True

    return False


# ============================================================
# PREZZI
# ============================================================

def parse_price(text):

    if not text:
        return None, None

    text = clean_text(
        text
    )

    patterns = [

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

        (
            r"(?:US\s*\$|\$|USD)\s*"
            r"([0-9]{1,5}(?:[.,][0-9]{1,2})?)",
            "USD"
        ),

        (
            r"(?:£|GBP)\s*"
            r"([0-9]{1,5}(?:[.,][0-9]{1,2})?)",
            "GBP"
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

        if (
            "," in raw
            and
            "." in raw
        ):

            raw = raw.replace(
                ".",
                ""
            )

            raw = raw.replace(
                ",",
                "."
            )

        elif "," in raw:

            raw = raw.replace(
                ",",
                "."
            )

        try:

            return (
                float(raw),
                currency
            )

        except ValueError:

            continue

    return None, None


# ============================================================
# EBAY SOLD ITEMS
# ============================================================

def search_ebay_sold():

    items = []

    for search in SEARCHES:

        print(
            f"[eBay SOLD] Ricerca: {search}"
        )

        # LH_Sold=1 = Sold Items
        url = (
            "https://www.ebay.com/sch/i.html?"
            "_nkw="
            + quote_plus(search)
            + "&LH_Sold=1"
            + "&LH_Complete=1"
            + "&_sop=13"
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
                f"[eBay SOLD] errore: {error}"
            )

            continue

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # ----------------------------------------------------
        # RISULTATI EBAY
        # ----------------------------------------------------

        results = soup.select(
            "li.s-item"
        )

        print(
            f"[eBay SOLD] "
            f"Risultati pagina: {len(results)}"
        )

        for result in results:

            title_element = result.select_one(
                ".s-item__title"
            )

            link_element = result.select_one(
                "a.s-item__link"
            )

            price_element = result.select_one(
                ".s-item__price"
            )

            if not title_element:
                continue

            if not link_element:
                continue

            title = clean_text(
                title_element.get_text(
                    " ",
                    strip=True
                )
            )

            # eBay può inserire "Shop on eBay"
            # come primo risultato
            if title.lower() in (
                "shop on ebay",
                "shop on eBay".lower()
            ):
                continue

            url_item = normalize_url(
                link_element.get(
                    "href",
                    ""
                )
            )

            price_text = ""

            if price_element:

                price_text = clean_text(
                    price_element.get_text(
                        " ",
                        strip=True
                    )
                )

            # ------------------------------------------------
            # CONTROLLO STATO VENDUTO
            # ------------------------------------------------

            result_text = clean_text(
                result.get_text(
                    " ",
                    strip=True
                )
            ).lower()

            sold_indicator = (

                "sold"
                in result_text

                or

                "venduto"
                in result_text

            )

            if not sold_indicator:

                print(
                    f"[SCARTATO] "
                    f"Non verificato come venduto: "
                    f"{title}"
                )

                continue

            # ------------------------------------------------
            # CONTROLLO COLLEZIONE
            # ------------------------------------------------

            if not is_relevant(
                title,
                result_text,
                url_item
            ):

                print(
                    f"[SCARTATO] "
                    f"Non Minecraft × Fossil: "
                    f"{title}"
                )

                continue

            # ------------------------------------------------
            # PREZZO
            # ------------------------------------------------

            price, currency = parse_price(
                price_text
            )

            # Se non abbiamo un prezzo verificabile,
            # NON notifichiamo.
            if price is None:

                print(
                    f"[SCARTATO] "
                    f"Prezzo non verificabile: "
                    f"{title}"
                )

                continue

            # ------------------------------------------------
            # ID
            # ------------------------------------------------

            item_id = ""

            data_view = result.get(
                "data-view"
            )

            if data_view:

                item_id = data_view

            if not item_id:

                item_id = url_item

            items.append({

                "id":
                f"ebay-sold:{item_id}",

                "source":
                "eBay",

                "title":
                title,

                "url":
                url_item,

                "price":
                price,

                "currency":
                currency,

                "kind":
                "sold",

                "sold":
                True,

                "found_at":
                datetime.now(
                    timezone.utc
                ).isoformat()
            })

    return dedupe_items(
        items
    )


# ============================================================
# DEDUPLICAZIONE
# ============================================================

def dedupe_items(items):

    output = []

    seen = set()

    for item in items:

        key = (
            item.get("id")
            or
            item.get("url")
        )

        if key in seen:

            continue

        seen.add(
            key
        )

        output.append(
            item
        )

    return output


# ============================================================
# FORMATTAZIONE PREZZO
# ============================================================

def format_price(item):

    price = item.get(
        "price"
    )

    currency = item.get(
        "currency"
    )

    if price is None:

        return "Prezzo non disponibile"

    symbols = {

        "EUR": "€",
        "USD": "$",
        "GBP": "£"
    }

    symbol = symbols.get(
        currency,
        currency or ""
    )

    return (
        f"{symbol}"
        f"{price:.2f}"
    )


# ============================================================
# NOTIFICA VENDITA
# ============================================================

def send_sold_notification(item):

    message = (

        "🟢 MINECRAFT × FOSSIL — VENDUTO\n\n"

        f"⌚ {item['title']}\n\n"

        f"💰 PREZZO DI VENDITA: "
        f"{format_price(item)}\n"

        f"🛒 Marketplace: eBay\n\n"

        f"🔗 {item['url']}"
    )

    send_telegram(
        message
    )


# ============================================================
# STORICO VENDITE
# ============================================================

def save_sale_history(
    items,
    now
):

    history = load_json(
        HISTORY_FILE,
        {}
    )

    date_key = (
        now.date().isoformat()
    )

    if date_key not in history:

        history[date_key] = []

    for item in items:

        history[date_key].append({

            "id":
            item["id"],

            "title":
            item["title"],

            "price":
            item["price"],

            "currency":
            item["currency"],

            "source":
            "eBay",

            "url":
            item["url"]
        })

    # --------------------------------------------------------
    # Evita duplicati nello storico
    # --------------------------------------------------------

    unique = {}

    for sale in history[date_key]:

        unique[
            sale["id"]
        ] = sale

    history[date_key] = list(
        unique.values()
    )

    # --------------------------------------------------------
    # Mantieni 180 giorni
    # --------------------------------------------------------

    keys = sorted(
        history.keys()
    )

    for key in keys[:-180]:

        del history[key]

    save_json(
        HISTORY_FILE,
        history
    )

    return history


# ============================================================
# REPORT GIORNALIERO
# ============================================================

def build_daily_report(
    today_sales,
    now,
    history
):

    prices = [

        sale["price"]

        for sale in today_sales

        if (
            sale.get("price")
            is not None
            and
            sale.get("currency")
            == "EUR"
        )
    ]

    lines = [

        "📊 MINECRAFT × FOSSIL "
        "— MARKET REPORT",

        f"📅 {now.strftime('%d/%m/%Y')}",

        ""
    ]

    if not prices:

        lines.append(
            "Nessuna vendita verificata "
            "rilevata oggi."
        )

    else:

        lines.append(
            f"🛒 Vendite verificate: "
            f"{len(prices)}"
        )

        lines.append(
            f"💰 Minimo: "
            f"€{min(prices):.2f}"
        )

        lines.append(
            f"📊 Media: "
            f"€{statistics.mean(prices):.2f}"
        )

        lines.append(
            f"💎 Massimo: "
            f"€{max(prices):.2f}"
        )

    lines.append("")

    lines.append(
        "ℹ️ Sono considerate solo vendite "
        "verificate su eBay."
    )

    lines.append(
        "Gli annunci ancora attivi "
        "NON vengono conteggiati."
    )

    return "\n".join(
        lines
    )


# ============================================================
# REPORT GIORNALIERO
# ============================================================

def should_send_daily_report(now):

    if now.hour < 18:

        return False

    state = load_json(
        REPORT_STATE_FILE,
        {}
    )

    return (
        state.get(
            "last_report_date"
        )
        !=
        now.date().isoformat()
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
    # TEST TELEGRAM
    # --------------------------------------------------------

    if (
        os.environ.get(
            "FORCE_NOTIFY",
            "false"
        ).lower()
        == "true"
    ):

        send_telegram(
            "🧪 Test: "
            "LE1252 Sold Market Radar "
            "funziona correttamente."
        )

        print(
            "Test Telegram inviato."
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
        f"🕐 Radar vendite: "
        f"{now.isoformat()}"
    )

    # --------------------------------------------------------
    # ELEMENTI GIÀ VISTI
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
    # RICERCA
    # --------------------------------------------------------

    print(
        "🔎 Cerco SOLO vendite "
        "Minecraft × Fossil..."
    )

    current_sales = (
        search_ebay_sold()
    )

    print(
        f"🟢 Vendite rilevanti "
        f"trovate: "
        f"{len(current_sales)}"
    )

    # --------------------------------------------------------
    # NUOVE VENDITE
    # --------------------------------------------------------

    new_sales = []

    for sale in current_sales:

        key = (
            sale["id"]
            or
            sale["url"]
        )

        if key not in seen:

            new_sales.append(
                sale
            )

            seen[key] = {

                "first_seen":
                now.isoformat(),

                "title":
                sale["title"],

                "price":
                sale["price"],

                "currency":
                sale["currency"],

                "source":
                "eBay",

                "url":
                sale["url"],

                "sold":
                True
            }

    # --------------------------------------------------------
    # NOTIFICHE
    # --------------------------------------------------------

    for sale in new_sales:

        try:

            send_sold_notification(
                sale
            )

            print(
                f"🔔 VENDITA NOTIFICATA: "
                f"{sale['title']} "
                f"— "
                f"{format_price(sale)}"
            )

        except Exception as error:

            print(
                f"❌ Errore Telegram: "
                f"{error}"
            )

    # --------------------------------------------------------
    # SALVA VISTI
    # --------------------------------------------------------

    save_json(
        SEEN_FILE,
        seen
    )

    # --------------------------------------------------------
    # STORICO
    # --------------------------------------------------------

    history = save_sale_history(
        current_sales,
        now
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    if should_send_daily_report(
        now
    ):

        try:

            today_key = (
                now.date().isoformat()
            )

            today_sales = [
                sale
                for sale in current_sales
                if sale.get("price")
                is not None
            ]

            report = build_daily_report(
                today_sales,
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
                f"❌ Errore report: "
                f"{error}"
            )

    # --------------------------------------------------------
    # FINE
    # --------------------------------------------------------

    print(
        f"🆕 Nuove vendite: "
        f"{len(new_sales)}"
    )


# ============================================================
# AVVIO
# ============================================================

if __name__ == "__main__":

    main()
