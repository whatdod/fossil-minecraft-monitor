import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests


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
# EBAY OAUTH
# ============================================================

def ebay_get_application_token():
    """
    Ottiene un Application Access Token eBay
    tramite Client Credentials Grant.

    Questo token è quello necessario per le Buy APIs.
    """

    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        print("⚠️ eBay: EBAY_CLIENT_ID o EBAY_CLIENT_SECRET mancanti.")
        return None

    token_url = "https://api.ebay.com/identity/v1/oauth2/token"

    try:

        response = session.post(
            token_url,
            auth=(EBAY_CLIENT_ID, EBAY_CLIENT_SECRET),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "client_credentials",
                "scope": (
                    "https://api.ebay.com/oauth/api_scope"
                ),
            },
            timeout=30,
        )

        if response.status_code != 200:

            print(
                f"[eBay OAuth] errore HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

            return None

        data = response.json()

        token = data.get("access_token")

        if not token:
            print("[eBay OAuth] risposta senza access_token.")
            return None

        print("✅ eBay OAuth: token ottenuto.")

        return token

    except requests.RequestException as e:

        print(f"[eBay OAuth] errore di connessione: {e}")

        return None

    except Exception as e:

        print(f"[eBay OAuth] errore: {e}")

        return None


# ============================================================
# EBAY MARKETPLACE INSIGHTS
# ============================================================

def ebay_marketplace_insights_search(token, keyword):
    """
    Cerca gli articoli VENDUTI tramite Marketplace Insights.

    Questa API restituisce la cronologia delle vendite eBay.
    È una API a accesso limitato.

    Se il token non dispone dei permessi necessari,
    restituiamo None per distinguere:

        None = fonte non disponibile / non autorizzata
        []   = fonte funzionante ma nessun risultato
    """

    url = (
        "https://api.ebay.com/"
        "buy/marketplace_insights/v1_beta/item_sales/search"
    )

    # Ultimi 90 giorni: è il limite previsto dalla API.
    end_time = utc_now()
    start_time = end_time - timedelta(days=89)

    start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_iso = end_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }

    params = {
        "q": keyword,
        "filter": f"lastSoldDate:[{start_iso}..{end_iso}]",
        "limit": 100,
        "offset": 0,
    }

    try:

        response = session.get(
            url,
            headers=headers,
            params=params,
            timeout=30,
        )

        if response.status_code in (401, 403):

            print(
                "[eBay Insights] ACCESSO NEGATO "
                f"(HTTP {response.status_code})."
            )

            try:
                print(response.json())
            except Exception:
                print(response.text[:500])

            return None

        if response.status_code != 200:

            print(
                f"[eBay Insights] errore HTTP "
                f"{response.status_code}: {response.text[:500]}"
            )

            return None

        data = response.json()

        return data

    except requests.RequestException as e:

        print(f"[eBay Insights] errore di connessione: {e}")

        return None

    except Exception as e:

        print(f"[eBay Insights] errore: {e}")

        return None


def extract_ebay_sales(data):
    """
    Estrae le vendite dal JSON restituito da eBay.
    """

    if not isinstance(data, dict):
        return []

    item_sales = data.get("itemSales", [])

    if not isinstance(item_sales, list):
        return []

    results = []

    for item in item_sales:

        if not isinstance(item, dict):
            continue

        title = (
            item.get("title")
            or item.get("itemTitle")
            or ""
        )

        if not is_relevant(title):
            continue

        item_id = (
            item.get("itemId")
            or item.get("legacyItemId")
            or item.get("item_id")
        )

        if not item_id:
            continue

        price, currency = parse_price(
            item.get("price")
        )

        if price is None:
            continue

        sold_date = (
            item.get("lastSoldDate")
            or item.get("soldDate")
            or item.get("itemEndDate")
            or ""
        )

        url = (
            item.get("itemWebUrl")
            or item.get("itemUrl")
            or ""
        )

        result = {
            "id": f"ebay-{item_id}",
            "title": title.strip(),
            "price": price,
            "currency": currency or "",
            "source": "eBay",
            "url": url,
            "sold_date": sold_date,
        }

        results.append(result)

    return results


def search_ebay_sales():
    """
    Esegue tutte le query eBay.

    Restituisce:
        None -> eBay non disponibile
        []   -> eBay disponibile, zero vendite
        list -> vendite trovate
    """

    print("\n🔎 eBay: ricerca tramite API delle vendite concluse...")

    token = ebay_get_application_token()

    if not token:
        return None

    all_sales = {}
    successful_queries = 0

    for keyword in SEARCHES:

        print(f"[eBay Insights] query: {keyword}")

        data = ebay_marketplace_insights_search(
            token,
            keyword,
        )

        if data is None:
            continue

        successful_queries += 1

        sales = extract_ebay_sales(data)

        for sale in sales:
            all_sales[sale["id"]] = sale

    if successful_queries == 0:

        print(
            "🔴 eBay: nessuna query ha restituito dati "
            "utilizzabili."
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
