import json
import os
import re
import statistics
from datetime import datetime
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
FORCE_NOTIFY = os.getenv("FORCE_NOTIFY", "").lower() == "true"

ROME = ZoneInfo("Europe/Rome")

SEARCHES = [
    '"LE1252"',
    '"Fossil Minecraft" watch',
    '"Minecraft Fossil" watch',
    '"Minecraft x Fossil"',
    '"Fossil x Minecraft"',
    '"Fossil Minecraft" "The End"',
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

EXCLUDED_BRANDS = [
    "louis vuitton",
    "gucci",
    "prada",
    "chanel",
    "hermes",
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
# UTILITÀ
# ============================================================

def normalize(text):
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def is_relevant(title):
    """
    Filtro MOLTO restrittivo.
    Accetta:
    - LE1252
    - Minecraft + Fossil + termini espliciti della collaborazione/prodotto
    """

    t = normalize(title)

    if not t:
        return False

    # Escludiamo marchi completamente estranei.
    if any(brand in t for brand in EXCLUDED_BRANDS):
        return False

    # Il codice prodotto è il match più forte.
    if re.search(r"\ble1252\b", t):
        return True

    # Devono comparire entrambi.
    if "minecraft" not in t or "fossil" not in t:
        return False

    collaboration_terms = [
        "minecraft x fossil",
        "fossil x minecraft",
        "fossil minecraft",
        "minecraft fossil",
    ]

    product_terms = [
        "watch",
        "chrono",
        "chronograph",
        "the end",
        "ender",
        "creeper",
        "limited edition",
        "limited",
        "collection",
    ]

    has_collaboration = any(x in t for x in collaboration_terms)
    has_product = any(x in t for x in product_terms)

    return has_collaboration and has_product


def parse_price(text):
    """
    Restituisce:
        (prezzo, valuta)
    """

    if not text:
        return None, None

    text = text.replace("\xa0", " ")

    patterns = [
        (r"(?:€|EUR)\s*([0-9][0-9\.,]*)", "EUR"),
        (r"([0-9][0-9\.,]*)\s*(?:€|EUR)", "EUR"),
        (r"(?:US\$|\$)\s*([0-9][0-9\.,]*)", "USD"),
        (r"(?:£|GBP)\s*([0-9][0-9\.,]*)", "GBP"),
        (r"(?:¥|JPY)\s*([0-9][0-9\.,]*)", "JPY"),
    ]

    for pattern, currency in patterns:

        match = re.search(pattern, text, re.I)

        if not match:
            continue

        raw = match.group(1)

        # 1.234,56
        if "," in raw and "." in raw:

            if raw.rfind(",") > raw.rfind("."):
                raw = raw.replace(".", "")
                raw = raw.replace(",", ".")
            else:
                raw = raw.replace(",", "")

        # 1234,56 oppure 1,234
        elif "," in raw:

            parts = raw.split(",")

            if len(parts[-1]) == 2:
                raw = raw.replace(",", ".")
            else:
                raw = raw.replace(",", "")

        # 1.234 oppure 1234.56
        elif "." in raw:

            parts = raw.split(".")

            if len(parts[-1]) == 3 and len(parts) > 1:
                raw = raw.replace(".", "")

        try:
            return float(raw), currency

        except ValueError:
            pass

    return None, None


# ============================================================
# HTTP
# ============================================================

def request_page(url, source):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True,
        )

        response.raise_for_status()

        return response.text

    except requests.RequestException as exc:

        print(
            f"[{source}] accesso non riuscito: {exc}"
        )

        return None


# ============================================================
# EBAY
# ============================================================

def search_ebay_sold():

    results = []

    print("🔎 eBay: ricerca SOLO venduti...")

    for query in SEARCHES:

        url = (
            "https://www.ebay.com/sch/i.html?"
            f"_nkw={quote_plus(query)}"
            "&LH_Sold=1"
            "&LH_Complete=1"
            "&_sop=13"
        )

        html = request_page(url, "eBay")

        # 403 = impossibile verificare.
        # NON significa zero vendite.
        if not html:
            continue

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        for item in soup.select("li.s-item"):

            title_el = item.select_one(
                ".s-item__title"
            )

            price_el = item.select_one(
                ".s-item__price"
            )

            link_el = item.select_one(
                "a.s-item__link"
            )

            if (
                not title_el
                or not price_el
                or not link_el
            ):
                continue

            title = title_el.get_text(
                " ",
                strip=True
            )

            text = item.get_text(
                " ",
                strip=True
            )

            # IMPORTANTISSIMO:
            # deve esserci uno stato esplicito di vendita.
            if not re.search(
                r"\b(sold|venduto|venduta)\b",
                text,
                re.I,
            ):
                continue

            if not is_relevant(title):
                continue

            price, currency = parse_price(
                price_el.get_text(
                    " ",
                    strip=True
                )
            )

            if price is None:
                continue

            item_url = link_el.get("href")

            if not item_url:
                continue

            results.append(
                {
                    "id": (
                        f"ebay:"
                        f"{item_url.split('?')[0]}"
                    ),
                    "title": title,
                    "price": price,
                    "currency": currency,
                    "source": "eBay",
                    "url": item_url,
                    "date": datetime.now(
                        ROME
                    ).isoformat(),
                }
            )

    return list(
        {
            x["id"]: x
            for x in results
        }.values()
    )


# ============================================================
# MERCARI
# ============================================================

def search_mercari_sold():

    results = []

    print(
        "🔎 Mercari: "
        "ricerca SOLO venduti espliciti..."
    )

    for query in SEARCHES:

        url = (
            "https://www.mercari.com/search/"
            f"?keyword={quote_plus(query)}"
        )

        html = request_page(
            url,
            "Mercari"
        )

        if not html:
            continue

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        for link in soup.find_all(
            "a",
            href=True
        ):

            title = link.get_text(
                " ",
                strip=True
            )

            if not title:
                continue

            if not is_relevant(title):
                continue

            parent = link

            for _ in range(4):

                if parent.parent:
                    parent = parent.parent

            text = parent.get_text(
                " ",
                strip=True
            )

            # Solo stato esplicito.
            if not re.search(
                r"\b(sold|sold out)\b",
                text,
                re.I,
            ):
                continue

            price, currency = parse_price(
                text
            )

            if price is None:
                continue

            href = link.get("href")

            if href.startswith("/"):
                href = (
                    "https://www.mercari.com"
                    + href
                )

            results.append(
                {
                    "id": (
                        f"mercari:"
                        f"{href.split('?')[0]}"
                    ),
                    "title": title,
                    "price": price,
                    "currency": currency,
                    "source": "Mercari",
                    "url": href,
                    "date": datetime.now(
                        ROME
                    ).isoformat(),
                }
            )

    return list(
        {
            x["id"]: x
            for x in results
        }.values()
    )


# ============================================================
# JSON
# ============================================================

def load_json(filename, default):

    try:

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):

        return default


def save_json(filename, data):

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# COMPATIBILITÀ market_history.json
# ============================================================

def normalize_history(raw):

    """
    Converte eventuali vecchi formati
    in questo formato:

    {
        "2026-09-04": [
            {
                "id": "...",
                "title": "...",
                "price": 123,
                ...
            }
        ]
    }

    Gli elementi corrotti/vecchi vengono ignorati
    invece di far andare in crash il bot.
    """

    if not isinstance(raw, dict):
        return {}

    normalized = {}

    for day, value in raw.items():

        if not isinstance(value, list):
            continue

        cleaned = []

        for item in value:

            if not isinstance(item, dict):
                continue

            if not item.get("id"):
                continue

            if not item.get("title"):
                continue

            if item.get("price") is None:
                continue

            cleaned.append(item)

        if cleaned:

            normalized[day] = list(
                {
                    x["id"]: x
                    for x in cleaned
                }.values()
            )

    return normalized


def save_history(current, now):

    raw = load_json(
        "market_history.json",
        {}
    )

    history = normalize_history(raw)

    day = now.strftime(
        "%Y-%m-%d"
    )

    merged = (
        history.get(day, [])
        + current
    )

    history[day] = list(
        {
            x["id"]: x
            for x in merged
        }.values()
    )

    save_json(
        "market_history.json",
        history
    )

    return history


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not BOT_TOKEN or not CHAT_ID:

        print(
            "⚠️ BOT_TOKEN/CHAT_ID "
            "non configurati."
        )

        return False

    try:

        response = requests.post(
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/sendMessage",

            json={
                "chat_id": CHAT_ID,
                "text": message,
                "disable_web_page_preview": False,
            },

            timeout=20,
        )

        response.raise_for_status()

        return True

    except requests.RequestException as exc:

        print(
            f"⚠️ Telegram: "
            f"errore invio: {exc}"
        )

        return False


# ============================================================
# NOTIFICHE
# ============================================================

def notify_new_sales(current):

    seen = load_json(
        "seen_items.json",
        {}
    )

    if not isinstance(seen, dict):
        seen = {}

    sent = 0

    for item in current:

        if (
            item["id"] in seen
            and not FORCE_NOTIFY
        ):
            continue

        message = (
            "🟢 MINECRAFT × FOSSIL — VENDUTO\n\n"
            f"⌚ {item['title']}\n\n"
            f"💰 PREZZO EFFETTIVO DI VENDITA: "
            f"{item['price']:.2f} "
            f"{item['currency']}\n"
            f"🛒 Marketplace: "
            f"{item['source']}\n"
            f"📅 Data rilevazione: "
            f"{item['date']}\n\n"
            f"🔗 {item['url']}"
        )

        if send_telegram(message):

            seen[item["id"]] = {
                "notified_at": (
                    datetime.now(
                        ROME
                    ).isoformat()
                )
            }

            sent += 1

    save_json(
        "seen_items.json",
        seen
    )

    return sent


# ============================================================
# REPORT GIORNALIERO
# ============================================================

def send_daily_report(
    history,
    now
):

    state = load_json(
        "report_state.json",
        {}
    )

    if not isinstance(state, dict):
        state = {}

    today = now.strftime(
        "%Y-%m-%d"
    )

    if state.get(
        "last_report"
    ) == today:

        return

    if now.hour < 18:
        return

    sales = []

    for items in history.values():

        if isinstance(items, list):

            sales.extend(
                x
                for x in items
                if isinstance(x, dict)
            )

    eur_sales = [
        x["price"]
        for x in sales
        if (
            x.get("currency") == "EUR"
            and isinstance(
                x.get("price"),
                (int, float)
            )
        )
    ]

    by_source = {}

    for item in sales:

        source = item.get(
            "source",
            "Altro"
        )

        by_source[source] = (
            by_source.get(
                source,
                0
            ) + 1
        )

    if eur_sales:

        prices = (
            f"Min: €{min(eur_sales):.2f}\n"
            f"Media: €{statistics.mean(eur_sales):.2f}\n"
            f"Max: €{max(eur_sales):.2f}"
        )

    else:

        prices = (
            "Nessuna vendita "
            "in EUR disponibile."
        )

    source_text = "\n".join(
        f"• {source}: {count}"
        for source, count
        in sorted(by_source.items())
    )

    if not source_text:
        source_text = "• Nessuna"

    message = (
        "📊 REPORT GIORNALIERO — "
        "MINECRAFT × FOSSIL\n\n"

        f"🛒 Vendite verificabili "
        f"registrate: {len(sales)}\n\n"

        f"💰 Prezzi EUR\n"
        f"{prices}\n\n"

        f"📦 Per marketplace\n"
        f"{source_text}\n\n"

        "ℹ️ Sono incluse esclusivamente "
        "vendite con stato esplicito "
        "e prezzo effettivo "
        "pubblicamente leggibile.\n"

        "Annunci attivi, prezzi richiesti "
        "e annunci semplicemente scomparsi "
        "NON vengono considerati vendite.\n"

        "Vinted non viene usato per notificare "
        "vendite perché il prezzo finale "
        "della transazione non è "
        "pubblicamente verificabile."
    )

    if send_telegram(message):

        state["last_report"] = today

        save_json(
            "report_state.json",
            state
        )


# ============================================================
# MAIN
# ============================================================

def main():

    now = datetime.now(ROME)

    print(
        f"🕐 Radar vendite: "
        f"{now.isoformat()}"
    )

    current = []

    # ----------------------------
    # eBay
    # ----------------------------

    ebay = search_ebay_sold()

    print(
        f"eBay vendite verificabili: "
        f"{len(ebay)}"
    )

    current.extend(ebay)

    # ----------------------------
    # Mercari
    # ----------------------------

    mercari = search_mercari_sold()

    print(
        f"Mercari vendite verificabili: "
        f"{len(mercari)}"
    )

    current.extend(mercari)

    # ----------------------------
    # Deduplicazione
    # ----------------------------

    current = list(
        {
            x["id"]: x
            for x in current
        }.values()
    )

    print(
        "🟢 Vendite Minecraft × Fossil "
        f"verificabili: {len(current)}"
    )

    # ----------------------------
    # Salvataggio
    # ----------------------------

    history = save_history(
        current,
        now
    )

    # ----------------------------
    # Telegram
    # ----------------------------

    sent = notify_new_sales(
        current
    )

    print(
        f"📨 Nuove notifiche inviate: "
        f"{sent}"
    )

    # ----------------------------
    # Report
    # ----------------------------

    send_daily_report(
        history,
        now
    )


if __name__ == "__main__":
    main()
