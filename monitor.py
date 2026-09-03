import os
import re
import json
import html
import statistics
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

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
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
}

# SOLO la nuova collezione Minecraft x Fossil.
SEARCHES = [
    '"LE1252"',
    '"Fossil Minecraft" watch',
    '"Minecraft Fossil" watch',
    '"Minecraft x Fossil"',
    '"Fossil x Minecraft"',
    '"Fossil Minecraft" "The End"',
]

EXCLUDED_BRANDS = [
    "louis vuitton", "gucci", "prada", "chanel", "hermes", "hermès",
    "rolex", "omega", "seiko", "casio", "citizen", "swatch", "timex",
    "tag heuer", "tissot", "cartier", "armani", "diesel", "versace",
    "bulgari", "breguet", "patek philippe", "audemars piguet",
]


def clean_text(text):
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def send_telegram(message):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": False},
        timeout=TIMEOUT,
    )
    r.raise_for_status()


def normalize_url(url):
    if not url:
        return ""
    url = html.unescape(url).strip()
    try:
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        if "uddg" in q:
            url = unquote(q["uddg"][0])
    except Exception:
        pass
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return ""
    return f"{p.scheme}://{p.netloc}{p.path}" + (f"?{p.query}" if p.query else "")


def is_relevant(title, description="", url=""):
    text = clean_text(f"{title} {description} {url}").lower()
    compact = re.sub(r"[^a-z0-9]", "", text)

    for brand in EXCLUDED_BRANDS:
        if brand in text and "le1252" not in compact:
            return False

    if "le1252" in compact:
        return True

    strong = [
        r"minecraft\s*x\s*fossil", r"fossil\s*x\s*minecraft",
        r"minecraft\s+×\s+fossil", r"fossil\s+×\s+minecraft",
        r"minecraft\s+and\s+fossil", r"fossil\s+and\s+minecraft",
        r"minecraft\s+fossil\s+collection", r"fossil\s+minecraft\s+collection",
        r"minecraft\s+fossil\s+watch", r"fossil\s+minecraft\s+watch",
        r"minecraft\s+fossil\s+chrono", r"fossil\s+minecraft\s+chrono",
        r"minecraft\s+fossil\s+chronograph", r"fossil\s+minecraft\s+chronograph",
        r"minecraft\s+fossil\s+the\s+end", r"fossil\s+minecraft\s+the\s+end",
        r"minecraft\s+fossil\s+ender", r"fossil\s+minecraft\s+ender",
    ]
    if any(re.search(p, text, re.I) for p in strong):
        return True

    return (
        "minecraft" in text
        and "fossil" in text
        and any(x in text for x in ["the end", "ender dragon", "minecraft watch", "minecraft chrono", "minecraft chronograph", "minecraft collection"])
    )


def parse_price(text):
    text = clean_text(text)
    patterns = [
        (r"(?:€|EUR)\s*([0-9]{1,5}(?:[.,][0-9]{1,2})?)", "EUR"),
        (r"([0-9]{1,5}(?:[.,][0-9]{1,2})?)\s*(?:€|EUR)", "EUR"),
        (r"(?:US\s*\$|\$|USD)\s*([0-9]{1,5}(?:[.,][0-9]{1,2})?)", "USD"),
        (r"(?:£|GBP)\s*([0-9]{1,5}(?:[.,][0-9]{1,2})?)", "GBP"),
        (r"(?:¥|JPY)\s*([0-9]{1,7}(?:[.,][0-9]{1,2})?)", "JPY"),
    ]
    for pattern, currency in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        raw = m.group(1)
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", ".")
        try:
            return float(raw), currency
        except ValueError:
            pass
    return None, None


def dedupe(items):
    out, seen = [], set()
    for item in items:
        key = item.get("id") or item.get("url")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def add_sale(items, source, title, url, price_text, description="", sale_id=None, sold_date=None):
    title = clean_text(title)
    url = normalize_url(url)
    description = clean_text(description)
    if not title or not url:
        return
    if not is_relevant(title, description, url):
        print(f"[SCARTATO] fuori collezione: {title}")
        return
    price, currency = parse_price(price_text)
    if price is None:
        print(f"[SCARTATO] prezzo vendita non verificabile: {title}")
        return
    items.append({
        "id": sale_id or f"{source}:{url}",
        "source": source,
        "title": title,
        "url": url,
        "price": price,
        "currency": currency,
        "kind": "sold",
        "sold": True,
        "sold_date": sold_date,
    })


def search_ebay_sold():
    items = []
    for query in SEARCHES:
        url = (
            "https://www.ebay.com/sch/i.html?"
            "_nkw=" + quote_plus(query) +
            "&LH_Sold=1&LH_Complete=1&_sop=13"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[eBay] errore: {e}")
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        for result in soup.select("li.s-item"):
            title_el = result.select_one(".s-item__title")
            link_el = result.select_one("a.s-item__link")
            price_el = result.select_one(".s-item__price")
            if not title_el or not link_el or not price_el:
                continue

            title = clean_text(title_el.get_text(" ", strip=True))
            if title.lower() == "shop on ebay":
                continue

            result_text = clean_text(result.get_text(" ", strip=True)).lower()
            # Richiediamo un'indicazione esplicita di vendita.
            if "sold" not in result_text and "venduto" not in result_text:
                continue

            link = link_el.get("href", "")
            price_text = clean_text(price_el.get_text(" ", strip=True))
            item_id = ""
            m = re.search(r"/itm/(?:[^/]+/)?(\d+)", link)
            if m:
                item_id = m.group(1)

            add_sale(
                items, "eBay", title, link, price_text,
                description=result_text,
                sale_id=f"ebay-sold:{item_id or normalize_url(link)}",
            )
    return dedupe(items)


def search_mercari_sold():
    """Mercari: usa esclusivamente risultati marcati SOLD.
    Se la pagina non espone chiaramente SOLD + prezzo, non notifichiamo.
    """
    items = []
    # Mercari US; la disponibilità della cronologia varia per mercato.
    for query in SEARCHES:
        url = "https://www.mercari.com/search/?keyword=" + quote_plus(query)
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[Mercari] errore: {e}")
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        # Non interpretiamo una card scomparsa come vendita.
        # Cerchiamo solo card che contengano esplicitamente SOLD.
        for card in soup.select("a[href*='/item/'], a[href*='/us/item/']"):
            text = clean_text(card.get_text(" ", strip=True))
            if not re.search(r"\bSOLD\b|\bSOLD OUT\b", text, re.I):
                continue
            href = card.get("href", "")
            if href.startswith("/"):
                href = "https://www.mercari.com" + href
            title = clean_text(text)
            price, currency = parse_price(text)
            if price is None:
                continue
            add_sale(
                items, "Mercari", title, href, text,
                description=text,
                sale_id=f"mercari-sold:{normalize_url(href)}",
            )
    return dedupe(items)


def format_price(item):
    symbols = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}
    currency = item.get("currency") or ""
    return f"{symbols.get(currency, currency + ' ')}{item['price']:.2f}"


def send_sale_notification(item):
    message = (
        "🟢 MINECRAFT × FOSSIL — VENDUTO\n\n"
        f"⌚ {item['title']}\n\n"
        f"💰 PREZZO EFFETTIVO DI VENDITA: {format_price(item)}\n"
        f"🛒 Marketplace: {item['source']}\n"
        + (f"📅 Data vendita: {item['sold_date']}\n" if item.get("sold_date") else "")
        + f"\n🔗 {item['url']}"
    )
    send_telegram(message)


def save_history(sales, now):
    history = load_json(HISTORY_FILE, {})
    day = now.date().isoformat()
    history.setdefault(day, [])
    for sale in sales:
        history[day].append({
            "id": sale["id"], "title": sale["title"],
            "price": sale["price"], "currency": sale["currency"],
            "source": sale["source"], "url": sale["url"],
        })
    unique = {x["id"]: x for x in history[day]}
    history[day] = list(unique.values())
    for key in sorted(history)[:-180]:
        del history[key]
    save_json(HISTORY_FILE, history)
    return history


def should_send_daily_report(now):
    if now.hour < 18:
        return False
    state = load_json(REPORT_STATE_FILE, {})
    return state.get("last_report_date") != now.date().isoformat()


def send_daily_report(sales, now):
    eur = [x["price"] for x in sales if x.get("currency") == "EUR"]
    lines = [
        "📊 MINECRAFT × FOSSIL — MARKET REPORT",
        f"📅 {now.strftime('%d/%m/%Y')}", "",
    ]
    if not sales:
        lines.append("Nessuna vendita verificata rilevata oggi.")
    else:
        lines.append(f"🛒 Vendite verificate: {len(sales)}")
        if eur:
            lines += [
                f"💰 Minimo: €{min(eur):.2f}",
                f"📊 Media: €{statistics.mean(eur):.2f}",
                f"💎 Massimo: €{max(eur):.2f}",
            ]
        for source in sorted(set(x["source"] for x in sales)):
            n = sum(1 for x in sales if x["source"] == source)
            lines.append(f"• {source}: {n} vendita/e verificate")
    lines += [
        "",
        "ℹ️ Sono incluse solo vendite per cui il marketplace espone un prezzo di vendita verificabile.",
        "Gli annunci ancora attivi e i semplici prezzi richiesti NON vengono conteggiati.",
        "Vinted non viene usato per notificare vendite: non espone pubblicamente lo storico del prezzo finale.",
    ]
    send_telegram("\n".join(lines))


def main():
    if os.environ.get("FORCE_NOTIFY", "false").lower() == "true":
        send_telegram("🧪 Test: LE1252 Sold Market Radar funziona correttamente.")

    now = datetime.now(timezone.utc).astimezone(ITALY_TZ)
    print(f"🕐 Radar vendite: {now.isoformat()}")

    seen = load_json(SEEN_FILE, {})
    if not isinstance(seen, dict):
        seen = {}

    print("🔎 eBay: ricerca SOLO venduti...")
    ebay = search_ebay_sold()
    print(f"eBay vendite verificabili: {len(ebay)}")

    print("🔎 Mercari: ricerca SOLO venduti espliciti...")
    mercari = search_mercari_sold()
    print(f"Mercari vendite verificabili: {len(mercari)}")

    current = dedupe(ebay + mercari)
    print(f"🟢 Vendite Minecraft × Fossil verificabili: {len(current)}")

    new_sales = []
    for sale in current:
        key = sale["id"] or sale["url"]
        if key not in seen:
            new_sales.append(sale)
            seen[key] = {
                "first_seen": now.isoformat(),
                "source": sale["source"],
                "title": sale["title"],
                "url": sale["url"],
                "price": sale["price"],
                "currency": sale["currency"],
                "sold": True,
            }

    for sale in new_sales:
        try:
            send_sale_notification(sale)
            print(f"🔔 VENDITA: {sale['title']} — {format_price(sale)}")
        except Exception as e:
            print(f"❌ Telegram: {e}")

    save_json(SEEN_FILE, seen)
    history = save_history(current, now)

    if should_send_daily_report(now):
        try:
            today = history.get(now.date().isoformat(), [])
            send_daily_report(today, now)
            save_json(REPORT_STATE_FILE, {"last_report_date": now.date().isoformat()})
            print("📊 Report giornaliero inviato.")
        except Exception as e:
            print(f"❌ Report: {e}")

    print(f"🆕 Nuove vendite notificate: {len(new_sales)}")


if __name__ == "__main__":
    main()
