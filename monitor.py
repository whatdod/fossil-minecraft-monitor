import os
import re
import json
import html
import statistics
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SEEN_FILE = "seen_items.json"
HISTORY_FILE = "market_history.json"
REPORT_STATE_FILE = "report_state.json"
ITALY_TZ = ZoneInfo("Europe/Rome")
TIMEOUT = 25

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
}

QUERIES = [
    '"LE1252"',
    '"Fossil LE1252"',
    '"Minecraft Fossil" "The End" watch',
    '"Minecraft x Fossil" watch',
]


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


def clean_text(value):
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def normalize_url(url):
    if not url:
        return ""
    url = html.unescape(url).strip()
    # DuckDuckGo sometimes returns redirect URLs. Extract the actual target.
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and "uddg" in parse_qs(parsed.query):
        url = unquote(parse_qs(parsed.query)["uddg"][0])
        parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    # Keep query parameters because some marketplaces need them for a listing.
    return url


def parse_price(text):
    text = clean_text(text)
    patterns = [
        (r"(?:€|EUR)\s*([0-9]{1,5}(?:[.,][0-9]{1,2})?)", "EUR"),
        (r"([0-9]{1,5}(?:[.,][0-9]{1,2})?)\s*(?:€|EUR)", "EUR"),
        (r"(?:\$|USD)\s*([0-9]{1,5}(?:[.,][0-9]{1,2})?)", "USD"),
        (r"([0-9]{1,5}(?:[.,][0-9]{1,2})?)\s*(?:\$|USD)", "USD"),
    ]
    for pattern, currency in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        raw = match.group(1)
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", ".")
        try:
            return float(raw), currency
        except ValueError:
            pass
    return None, None


def relevant(title, description=""):
    text = clean_text(f"{title} {description}").lower().replace(" ", "")
    if "le1252" in text:
        return True
    full = clean_text(f"{title} {description}").lower()
    return (
        ("fossil" in full and "minecraft" in full)
        and any(x in full for x in ("watch", "orologio", "chrono", "cronografo", "the end"))
    )


def add_item(items, source, title, url, description="", price_text="", item_id=None, kind="listing", published=None):
    title = clean_text(title)
    description = clean_text(description)
    url = normalize_url(url)
    if not url or not title or not relevant(title, description):
        return
    price, currency = parse_price(price_text or f"{title} {description}")
    items.append({
        "id": item_id or url,
        "source": source,
        "title": title,
        "url": url,
        "price": price,
        "currency": currency,
        "kind": kind,
        "published": published,
    })


def search_duckduckgo(query, max_results=10):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[web] search failed: {exc}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    items = []
    for result in soup.select(".result")[:max_results]:
        link = result.select_one("a.result__a")
        if not link:
            continue
        title = clean_text(link.get_text(" ", strip=True))
        snippet_node = result.select_one(".result__snippet")
        snippet = clean_text(snippet_node.get_text(" ", strip=True)) if snippet_node else ""
        add_item(items, "Web", title, link.get("href", ""), description=snippet, kind="web")
    return items


def search_web():
    items = []
    for query in QUERIES:
        targets = [
            query,
            f"site:ebay.com {query}",
            f"site:ebay.it {query}",
            f"site:vinted.it {query}",
            f"site:vinted.com {query}",
            f"site:reddit.com {query}",
            f"{query} watch forum",
        ]
        for target in targets:
            items.extend(search_duckduckgo(target, 8))
    return dedupe(items)


def search_reddit():
    items = []
    for query in ["LE1252", '"Fossil" "Minecraft" watch']:
        rss_url = f"https://www.reddit.com/search.rss?q={quote_plus(query)}&sort=new&t=month"
        try:
            response = requests.get(rss_url, headers={**HEADERS, "Accept": "application/rss+xml"}, timeout=TIMEOUT)
            response.raise_for_status()
            root = ET.fromstring(response.text)
        except Exception as exc:
            print(f"[reddit] failed: {exc}")
            continue

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            title = entry.findtext("atom:title", default="", namespaces=ns)
            link_node = entry.find("atom:link", ns)
            link = link_node.get("href", "") if link_node is not None else ""
            content = entry.findtext("atom:content", default="", namespaces=ns)
            published = entry.findtext("atom:updated", default="", namespaces=ns)
            add_item(
                items, "Reddit", title, link,
                description=BeautifulSoup(content, "html.parser").get_text(" "),
                kind="discussion", published=published
            )
    return dedupe(items)


def ebay_token():
    client_id = os.getenv("EBAY_CLIENT_ID")
    client_secret = os.getenv("EBAY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    try:
        response = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["access_token"]
    except Exception as exc:
        print(f"[eBay] token failed: {exc}")
        return None


def search_ebay():
    token = ebay_token()
    if not token:
        print("[eBay] API credentials not configured; web search will cover eBay.")
        return []

    items = []
    endpoint = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    for query in ["LE1252", "Fossil Minecraft The End", "Minecraft Fossil watch"]:
        try:
            response = requests.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_IT",
                    "Accept": "application/json",
                },
                params={"q": query, "limit": 50},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            print(f"[eBay] search failed for {query}: {exc}")
            continue

        for item in data.get("itemSummaries", []):
            price = item.get("price") or {}
            add_item(
                items, "eBay", item.get("title", ""),
                item.get("itemWebUrl") or item.get("itemAffiliateWebUrl", ""),
                description=item.get("shortDescription", ""),
                price_text=f"{price.get('currency', '')} {price.get('value', '')}",
                item_id=f"ebay:{item.get('itemId')}" if item.get("itemId") else None,
                kind="listing",
            )
    return dedupe(items)


def dedupe(items):
    output = []
    seen = set()
    for item in items:
        key = item["id"] or item["url"]
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def source_of(item):
    url = item["url"].lower()
    source = item["source"].lower()
    if "ebay." in url or source == "ebay":
        return "eBay"
    if "vinted." in url:
        return "Vinted"
    if "reddit.com" in url or source == "reddit":
        return "Reddit"
    return "Web"


def price_label(item):
    if item.get("price") is None:
        return "Prezzo non rilevato"
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(item.get("currency"), f"{item.get('currency', '?')} ")
    return f"{symbol}{item['price']:.2f}"


def notify_new(item):
    source = source_of(item)
    if item["kind"] == "discussion":
        heading = "📰 NUOVA DISCUSSIONE"
    elif source == "eBay":
        heading = "🟢 NUOVA INSERZIONE"
    elif source == "Vinted":
        heading = "🟢 NUOVO ANNUNCIO"
    else:
        heading = "🔎 NUOVA SEGNALAZIONE"
    send_telegram(
        f"{heading}\n\n{source}: {item['title']}\n"
        f"Prezzo: {price_label(item)}\n\n{item['url']}"
    )


def daily_snapshot(items):
    result = {}
    for source in ["eBay", "Vinted", "Reddit", "Web"]:
        selected = [x for x in items if source_of(x) == source]
        prices = [x["price"] for x in selected if x.get("price") is not None and x.get("currency") == "EUR"]
        result[source] = {
            "count": len(selected),
            "priced_count": len(prices),
            "min": min(prices) if prices else None,
            "average": statistics.mean(prices) if prices else None,
            "max": max(prices) if prices else None,
        }
    return result


def save_history(items, today):
    history = load_json(HISTORY_FILE, {})
    history[today.isoformat()] = daily_snapshot(items)
    for key in sorted(history)[:-90]:
        del history[key]
    save_json(HISTORY_FILE, history)
    return history


def send_daily_report(items, new_items, history, now):
    today = now.date()
    yesterday = today - timedelta(days=1)
    snap = history.get(today.isoformat(), {})
    prev = history.get(yesterday.isoformat(), {})

    lines = [f"📊 LE1252 MARKET REPORT — {today.strftime('%d/%m/%Y')}", ""]
    for source in ["eBay", "Vinted", "Reddit", "Web"]:
        data = snap.get(source, {})
        count = data.get("count", 0)
        if source == "Reddit":
            lines.append(f"Reddit: {count} discussioni rilevate")
        elif data.get("average") is not None:
            lines.append(
                f"{source}: {count} risultati | min €{data['min']:.2f} | "
                f"media €{data['average']:.2f} | max €{data['max']:.2f}"
            )
        else:
            lines.append(f"{source}: {count} risultati | prezzi non rilevati")

    lines.append("")
    lines.append(f"🆕 Nuove segnalazioni oggi: {len(new_items)}")

    today_avgs = [d["average"] for d in snap.values() if d.get("average") is not None]
    prev_avgs = [d["average"] for d in prev.values() if d.get("average") is not None]
    if today_avgs and prev_avgs:
        current = statistics.mean(today_avgs)
        old = statistics.mean(prev_avgs)
        if old:
            change = (current - old) / old * 100
            lines.append(f"📈 Prezzo medio vs ieri: {'+' if change >= 0 else ''}{change:.1f}%")
    else:
        lines.append("📈 Prezzo medio vs ieri: dati storici insufficienti")

    lines += ["", "ℹ️ I prezzi sono prezzi richiesti (asking price), salvo esplicita indicazione di vendita conclusa."]
    send_telegram("\n".join(lines))


def main():
    now = datetime.now(timezone.utc).astimezone(ITALY_TZ)
    print(f"🕐 Radar LE1252: {now.isoformat()}")

    seen = load_json(SEEN_FILE, {})
    if not isinstance(seen, dict):
        seen = {}

    current = dedupe(search_ebay() + search_web() + search_reddit())
    print(f"🔎 Risultati rilevanti: {len(current)}")

    new_items = []
    for item in current:
        key = item["id"] or item["url"]
        if key not in seen:
            new_items.append(item)
            seen[key] = {
                "first_seen": now.isoformat(),
                "source": source_of(item),
                "title": item["title"],
                "url": item["url"],
                "price": item.get("price"),
                "currency": item.get("currency"),
            }

    for item in new_items:
        try:
            notify_new(item)
            print(f"🔔 Notificato: {item['title']}")
        except Exception as exc:
            print(f"❌ Telegram notification failed: {exc}")

    save_json(SEEN_FILE, seen)
    history = save_history(current, now.date())

    report_state = load_json(REPORT_STATE_FILE, {})
    if now.hour >= 18 and report_state.get("last_report_date") != now.date().isoformat():
        try:
            send_daily_report(current, new_items, history, now)
            report_state["last_report_date"] = now.date().isoformat()
            save_json(REPORT_STATE_FILE, report_state)
            print("📊 Report giornaliero inviato.")
        except Exception as exc:
            print(f"❌ Daily report failed: {exc}")

    print(f"🆕 Nuovi elementi: {len(new_items)}")


if __name__ == "__main__":
    main()
