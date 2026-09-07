import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURAZIONE
# ============================================================

ROME = ZoneInfo("Europe/Rome")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "").strip()
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "").strip()

FORCE_NOTIFY = os.getenv("FORCE_NOTIFY", "").lower() == "true"

SEEN_FILE = "seen_items.json"
HISTORY_FILE = "market_history.json"
REPORT_STATE_FILE = "report_state.json"


# ============================================================
# RICERCHE
# ============================================================

# Le query sono volutamente molto restrittive.
# L'obiettivo è trovare esclusivamente Minecraft × Fossil.

SEARCHES = [
    "LE1252",
    "Minecraft Fossil",
    "Fossil Minecraft",
    "Minecraft Fossil The End",
]


# Parole che devono identificare chiaramente la collaborazione.
REQUIRED_PRODUCT_TERMS = [
    "le1252",
    "minecraft",
]

REQUIRED_BRAND_TERMS = [
    "fossil",
]

# Brand/prodotti evidentemente estranei.
EXCLUDED_TERMS = [
    "louis vuitton",
    "gucci",
    "rolex",
    "omega",
    "casio",
    "seiko",
    "citizen",
    "swatch",
    "apple watch",
    "garmin",
]


# ============================================================
# SESSIONE HTTP
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": "LE1252-Market-Radar/1.0",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
)


# ============================================================
# UTILITY
# ============================================================

def now_rome():
    return datetime.now(ROME)


def utc_now():
    return datetime.now(timezone.utc)


def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(f"[JSON] impossibile leggere {filename}: {e}")
        return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_seen(data):
    """
    seen_items.json può essere:
    - lista di ID
    - dizionario
    - vecchio formato

    Lo trasformiamo sempre in un set di stringhe.
    """

    if isinstance(data, list):
        return {str(x) for x in data}

    if isinstance(data, dict):
        result = set()

        for key, value in data.items():
            if isinstance(key, str):
                result.add(key)

            if isinstance(value, str):
                result.add(value)

            elif isinstance(value, dict):
                for candidate in (
                    value.get("id"),
                    value.get("itemId"),
                    value.get("item_id"),
                ):
                    if candidate:
                        result.add(str(candidate))

        return result

    return set()


def normalize_history(data):
    """
    Normalizza market_history.json in:

    {
        "YYYY-MM-DD": [
            {
                "id": "...",
                "title": "...",
                "price": 123.45,
                "currency": "USD",
                "source": "eBay",
                "url": "...",
                "sold_date": "..."
            }
        ]
    }
    """

    if not isinstance(data, dict):
        return {}

    normalized = {}

    for day, entries in data.items():

        if not isinstance(entries, list):
            continue

        valid_entries = []

        for item in entries:

            if not isinstance(item, dict):
                continue

            item_id = (
                item.get("id")
                or item.get("itemId")
                or item.get("item_id")
            )

            title = item.get("title")

            if not item_id or not title:
                continue

            valid_entries.append(
                {
                    "id": str(item_id),
                    "title": str(title),
                    "price": item.get("price"),
                    "currency": item.get("currency"),
                    "source": item.get("source", "eBay"),
                    "url": item.get("url", ""),
                    "sold_date": item.get("sold_date", ""),
                }
            )

        if valid_entries:
            normalized[str(day)] = valid_entries

    return normalized


def is_relevant(title):
    """
    Filtro estremamente severo.
    Deve essere chiaramente Minecraft + Fossil
    oppure contenere LE1252.
    """

    text = (title or "").lower()

    for excluded in EXCLUDED_TERMS:
        if excluded in text:
            return False

    # Caso più sicuro: codice prodotto.
    if "le1252" in text:
        return True

    has_minecraft = "minecraft" in text
    has_fossil = "fossil" in text

    if has_minecraft and has_fossil:
        return True

    return False


def parse_price(price_obj):
    if not isinstance(price_obj, dict):
        return None, None

    value = price_obj.get("value")
    currency = price_obj.get("currency")

    if value is None:
        return None, currency

    try:
        return float(value), currency
    except Exception:
        return None, currency


# ============================================================
# EBAY - RICERCA VENDUTI (scraping pagina pubblica)
# ============================================================
#
# La Marketplace Insights API ufficiale di eBay è una API
# "Limited Release": eBay non concede più l'accesso a nuovi
# sviluppatori (non è un problema di credenziali sbagliate).
# Per questo motivo usiamo la pagina pubblica dei risultati
# "Venduto" di eBay, che non richiede alcuna autenticazione.

EBAY_SOLD_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9",
}


def ebay_scrape_sold_search(keyword):
    """
    Interroga la pagina pubblica eBay.it dei risultati
    "Venduto" per una keyword.

    Restituisce:
        None -> richiesta fallita (rete, blocco, HTTP != 200)
        list -> annunci venduti trovati (anche vuota)
    """

    url = "https://www.ebay.it/sch/i.html"

    params = {
        "_nkw": keyword,
        "LH_Sold": 1,
        "LH_Complete": 1,
        "_ipg": 100,
    }

    try:

        response = requests.get(
            url,
            headers=EBAY_SOLD_SCRAPE_HEADERS,
            params=params,
            timeout=30,
        )

        if response.status_code != 200:

            print(
                f"[eBay scrape] errore HTTP "
                f"{response.status_code} per '{keyword}'."
            )

            return None

        soup = BeautifulSoup(response.text, "html.parser")

        items = soup.select("li.s-item")

        results = []

        for item in items:

            title_el = item.select_one(".s-item__title")

            if not title_el:
                continue

            title = title_el.get_text(strip=True)

            if not title or title.lower().startswith("risultati per"):
                continue

            if not is_relevant(title):
                continue

            link_el = item.select_one("a.s-item__link")

            url_item = link_el["href"] if link_el and link_el.has_attr("href") else ""

            item_id_match = re.search(r"/itm/(\d+)", url_item)

            if not item_id_match:
                continue

            item_id = item_id_match.group(1)

            price_el = item.select_one(".s-item__price")

            price_text = price_el.get_text(strip=True) if price_el else ""

            price, currency = parse_ebay_price_text(price_text)

            sold_date = ""

            date_el = item.select_one(".s-item__caption--signal, .POSITIVE")

            if date_el:
                sold_date = date_el.get_text(strip=True)

            results.append(
                {
                    "id": f"ebay-{item_id}",
                    "title": title,
                    "price": price,
                    "currency": currency,
                    "source": "eBay",
                    "url": url_item.split("?")[0],
                    "sold_date": sold_date,
                }
            )

        return results

    except requests.RequestException as e:

        print(f"[eBay scrape] errore di connessione: {e}")

        return None

    except Exception as e:

        print(f"[eBay scrape] errore: {e}")

        return None


def parse_ebay_price_text(text):
    """
    Converte un testo tipo 'EUR 189,00' o '€ 189,00 a 210,00'
    in (valore_float, valuta). Se è un range prende il primo valore.
    """

    if not text:
        return None, ""

    currency = "EUR" if "€" in text or "EUR" in text.upper() else ""

    match = re.search(r"(\d+(?:[.,]\d+)?)", text.replace(".", "").replace(",", "."))

    if not match:
        return None, currency

    try:
        return float(match.group(1)), currency
    except Exception:
        return None, currency


def search_ebay_sales():
    """
    Esegue tutte le query eBay tramite scraping della
    pagina pubblica dei "Venduto".

    Restituisce:
        None -> eBay non raggiungibile per nessuna query
        []   -> eBay raggiungibile, zero vendite pertinenti
        list -> vendite trovate
    """

    print("\n🔎 eBay: ricerca annunci VENDUTO (pagina pubblica)...")

    all_sales = {}
    successful_queries = 0

    for keyword in SEARCHES:

        print(f"[eBay scrape] query: {keyword}")

        sales = ebay_scrape_sold_search(keyword)

        if sales is None:
            continue

        successful_queries += 1

        for sale in sales:
            all_sales[sale["id"]] = sale

    if successful_queries == 0:

        print(
            "🔴 eBay: nessuna query ha restituito dati "
            "utilizzabili (possibile blocco temporaneo)."
        )

        return None

    results = list(all_sales.values())

    print(
        f"🟢 eBay: {len(results)} vendite "
        "Minecraft × Fossil verificabili."
    )

    return results


# ============================================================
# SALVATAGGIO STORICO
# ============================================================

def save_history(sales):
    """
    Salva le vendite nel database storico senza duplicarle.
    """

    raw_history = load_json(
        HISTORY_FILE,
        {},
    )

    history = normalize_history(raw_history)

    today = now_rome().strftime("%Y-%m-%d")

    existing = history.get(today, [])

    merged = {}

    for item in existing:

        if isinstance(item, dict) and item.get("id"):
            merged[str(item["id"])] = item

    for sale in sales:

        if isinstance(sale, dict) and sale.get("id"):
            merged[str(sale["id"])] = sale

    history[today] = list(merged.values())

    save_json(
        HISTORY_FILE,
        history,
    )

    print(
        f"💾 Storico salvato: {len(history[today])} "
        f"vendite per {today}."
    )


# ============================================================
# NUOVE VENDITE
# ============================================================

def get_new_sales(sales):
    """
    Restituisce solo le vendite mai notificate prima.
    """

    raw_seen = load_json(
        SEEN_FILE,
        [],
    )

    seen = normalize_seen(raw_seen)

    new_sales = []

    for sale in sales:

        sale_id = str(sale.get("id", ""))

        if not sale_id:
            continue

        if sale_id not in seen:
            new_sales.append(sale)
            seen.add(sale_id)

    save_json(
        SEEN_FILE,
        sorted(seen),
    )

    return new_sales


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Telegram: BOT_TOKEN o CHAT_ID mancanti.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": False,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=30,
        )

        if response.status_code != 200:

            print(
                f"[Telegram] errore HTTP "
                f"{response.status_code}: "
                f"{response.text[:500]}"
            )

            return False

        return True

    except requests.RequestException as e:

        print(f"[Telegram] errore: {e}")

        return False


def format_sale_message(sale):
    title = sale.get("title", "Minecraft × Fossil")

    price = sale.get("price")
    currency = sale.get("currency", "")

    url = sale.get("url", "")

    sold_date = sale.get("sold_date", "")

    if isinstance(price, float):
        price_text = f"{price:.2f}"
    else:
        price_text = str(price)

    message = (
        "🟢 MINECRAFT × FOSSIL — VENDUTO\n\n"
        f"⌚ {title}\n\n"
        f"💰 PREZZO EFFETTIVO DI VENDITA: "
        f"{price_text} {currency}\n"
        f"🛒 Marketplace: eBay\n"
    )

    if sold_date:
        message += f"📅 Venduto: {sold_date}\n"

    if url:
        message += f"\n🔗 {url}"

    return message


def notify_new_sales(new_sales):
    count = 0

    for sale in new_sales:

        message = format_sale_message(sale)

        if send_telegram(message):
            count += 1

    return count


# ============================================================
# REPORT GIORNALIERO
# ============================================================

def should_send_daily_report():
    """
    Invia al massimo un report al giorno,
    dopo le 18:00 ora italiana.
    """

    now = now_rome()

    if now.hour < 18:
        return False

    state = load_json(
        REPORT_STATE_FILE,
        {},
    )

    today = now.strftime("%Y-%m-%d")

    if state.get("last_report") == today:
        return False

    return True


def save_report_state():
    today = now_rome().strftime("%Y-%m-%d")

    save_json(
        REPORT_STATE_FILE,
        {
            "last_report": today,
        },
    )


def send_daily_report(sales, ebay_available):
    if not should_send_daily_report():
        return

    today = now_rome().strftime("%Y-%m-%d")

    if not ebay_available:

        message = (
            "⚠️ MINECRAFT × FOSSIL — REPORT GIORNALIERO\n\n"
            f"📅 {today}\n\n"
            "🔴 eBay: dati vendite non disponibili.\n\n"
            "Il radar NON considera questo come "
            "\"0 vendite\".\n"
            "La fonte non è stata interrogabile "
            "oppure le API non dispongono dei permessi "
            "necessari."
        )

    elif not sales:

        message = (
            "📊 MINECRAFT × FOSSIL — REPORT GIORNALIERO\n\n"
            f"📅 {today}\n\n"
            "🟢 Nessuna vendita verificabile rilevata "
            "nelle fonti disponibili.\n\n"
            "Fonte: eBay Marketplace Insights."
        )

    else:

        lines = [
            "📊 MINECRAFT × FOSSIL — REPORT GIORNALIERO",
            "",
            f"📅 {today}",
            "",
            f"🟢 Vendite verificabili: {len(sales)}",
            "",
        ]

        for sale in sales:

            price = sale.get("price")
            currency = sale.get("currency", "")
            title = sale.get("title", "")

            if isinstance(price, float):
                price_text = f"{price:.2f}"
            else:
                price_text = str(price)

            lines.append(
                f"⌚ {title}\n"
                f"💰 {price_text} {currency}"
            )

            lines.append("")

        message = "\n".join(lines)

    if send_telegram(message):
        save_report_state()


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        f"🕐 Radar vendite: "
        f"{now_rome().isoformat()}"
    )

    print(
        "\n🎯 Obiettivo: "
        "SOLO Minecraft × Fossil"
    )

    print(
        "🎯 Regola: "
        "SOLO vendite realmente verificabili"
    )

    print(
        "🎯 Nessuna inferenza da annunci scomparsi"
    )

    # --------------------------------------------------------
    # eBay
    # --------------------------------------------------------

    sales = search_ebay_sales()

    ebay_available = sales is not None

    if sales is None:
        sales = []

    print(
        f"\n🟢 Vendite Minecraft × Fossil "
        f"verificabili: {len(sales)}"
    )

    # --------------------------------------------------------
    # Salvataggio storico
    # --------------------------------------------------------

    save_history(sales)

    # --------------------------------------------------------
    # Nuove notifiche
    # --------------------------------------------------------

    if ebay_available:

        new_sales = get_new_sales(sales)

        # FORCE_NOTIFY serve esclusivamente per il test manuale.
        if FORCE_NOTIFY and sales:

            print(
                "🧪 FORCE_NOTIFY attivo: "
                "invio una vendita di test."
            )

            notifications = notify_new_sales(
                sales[:1]
            )

        else:

            notifications = notify_new_sales(
                new_sales
            )

        print(
            f"📨 Nuove notifiche inviate: "
            f"{notifications}"
        )

    else:

        print(
            "📨 Nessuna notifica: "
            "eBay non è stato interrogabile."
        )

    # --------------------------------------------------------
    # Report giornaliero
    # --------------------------------------------------------

    send_daily_report(
        sales,
        ebay_available,
    )

    print("\n✅ Radar completato.")


if __name__ == "__main__":
    main()
