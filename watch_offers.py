#!/usr/bin/env python3
"""Busca anuncios en Vinted y Wallapop por debajo del precio máximo y envía la lista.

Usa un navegador automatizado (Playwright), porque las peticiones directas
a las APIs de las webs devuelven 403/404.

Uso: .venv/bin/python watch_offers.py [ruta/config.json]
Envía la lista completa a Telegram si están TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID.
Guarda lo ya visto en seen.json y añade el resumen a digest.md.
"""

import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE_DIR, "config.json")
SEEN_PATH = os.path.join(BASE_DIR, "seen.json")
DIGEST_PATH = os.path.join(BASE_DIR, "digest.md")
PRICE_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*€")
TELEGRAM_MAX_CHARS = 3800  # el límite de Telegram es 4096
MAX_OFFERS_PER_PRODUCT = 5


def parse_price(text):
    match = PRICE_RE.search(text or "")
    return float(match.group(1).replace(",", ".")) if match else None


def pause(config):
    time.sleep(config.get("delay_seconds", 3) + random.uniform(0, 2))


def search_vinted(page, domain, query, max_price):
    params = urllib.parse.urlencode({"search_text": query, "price_to": max_price})
    page.goto(f"https://{domain}/catalog?{params}", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_selector('[data-testid="grid-item"]', timeout=20000)
    cards = page.eval_on_selector_all(
        '[data-testid$="--overlay-link"]',
        """els => els.map(e => ({
            testid: e.getAttribute('data-testid'),
            href: e.getAttribute('href'),
            title: e.getAttribute('title') || ''
        }))""",
    )
    prices = page.eval_on_selector_all('[data-testid$="--price-text"]', "els => els.map(e => e.textContent)")
    results = []
    for card, price_text in zip(cards, prices):
        item_id = re.search(r"product-item-id-(\d+)", card["testid"])
        amount = parse_price(price_text)
        if not item_id or amount is None or amount > max_price:
            continue
        results.append({
            "site": "Vinted",
            "id": item_id.group(1),
            "title": card["title"].split(", Marca:")[0],
            "price": f"{amount:.2f} €",
            "url": f"https://{domain}{card['href'].split('?')[0]}",
        })
    return results


def search_wallapop(page, domain, query, max_price):
    params = urllib.parse.urlencode({"keywords": query, "max_sale_price": max_price})
    page.goto(f"https://{domain}/app/search?{params}", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(5000)
    anchors = page.eval_on_selector_all(
        'a[href*="/item/"]',
        "els => els.map(e => ({href: e.getAttribute('href'), text: e.innerText}))",
    )
    results = []
    seen_urls = set()
    for a in anchors:
        url = urllib.parse.urljoin(f"https://{domain}", a["href"]).split("?")[0]
        amount = parse_price(a["text"])
        if url in seen_urls or amount is None or amount > max_price:
            continue
        seen_urls.add(url)
        lines = [line for line in a["text"].splitlines() if line.strip()]
        results.append({
            "site": "Wallapop",
            "id": url.rsplit("/", 1)[-1],
            "title": lines[0] if lines else url,
            "price": f"{amount:.2f} €",
            "url": url,
        })
    return results


def fetch_description(page, url):
    """Lee la descripción del anuncio; si la web no la expone en meta, devuelve vacío."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        return page.evaluate(
            """() => {
                const meta = document.querySelector('meta[name="description"]')
                    || document.querySelector('meta[property="og:description"]');
                return meta ? meta.content : '';
            }"""
        )
    except Exception:
        return ""


def review_description(text, red_flags):
    """Devuelve las palabras de alerta que aparecen en el texto."""
    lowered = text.lower()
    return [flag for flag in red_flags if flag.lower() in lowered]


def run_chollos(page, config, seen, errors):
    """Compara los precios de cada término en las dos webs y devuelve los anuncios
    muy por debajo de la mediana del mercado (precio de referencia automático)."""
    cfg = config.get("chollos", {})
    deals = []
    for query in cfg.get("queries", []):
        listings = []
        for site, fn, domain in (
            ("Vinted", search_vinted, config["vinted_domain"]),
            ("Wallapop", search_wallapop, config["wallapop_domain"]),
        ):
            try:
                listings += fn(page, domain, query, 100000)
            except Exception as exc:
                errors.append(f"Chollos {site} / {query}: {str(exc)[:150]}")
            pause(config)
        if len(listings) < cfg.get("min_listings", 5):
            continue
        median = statistics.median(parse_price(i["price"]) for i in listings)
        query_deals = []
        for item in listings:
            price = parse_price(item["price"])
            key = f"chollo:{item['site']}:{item['id']}"
            if price <= median * (1 - cfg.get("discount", 0.4)) and key not in seen:
                seen.add(key)
                query_deals.append({**item, "query": query, "median": median, "discount": 1 - price / median})
        query_deals.sort(key=lambda d: d["discount"], reverse=True)
        best = query_deals[:MAX_OFFERS_PER_PRODUCT]
        if best:
            # se envían en cuanto está calculado el término, sin esperar al resto
            today = datetime.now().strftime("%Y-%m-%d %H:%M")
            send_summary(f"💎 Chollos · {today}\n\n" + "\n".join(build_deal_message(d) for d in best))
        deals.extend(best)
    return deals


def build_deal_message(deal):
    """Mensaje para un chollo: precio, mediana del mercado, título y enlace."""
    return "\n".join([
        f"💎 {deal['query']} · {deal['site']} · {deal['price']}",
        f"Mediana del mercado: {deal['median']:.0f} € (-{deal['discount'] * 100:.0f} %)",
        deal["title"],
        deal["url"],
    ]) + "\n"


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def send_telegram_text(text):
    """Envía un mensaje de texto plano al chat de Telegram."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def split_for_telegram(text):
    """Parte el texto en trozos que quepan en un mensaje de Telegram, cortando por líneas."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > TELEGRAM_MAX_CHARS:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


def send_summary(text):
    """Telegram si hay variables configuradas; si no, lo deja en la terminal y avisa en macOS."""
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        try:
            for chunk in split_for_telegram(text):
                send_telegram_text(chunk)
            return
        except Exception as exc:
            print(f"No se pudo enviar a Telegram: {exc}", file=sys.stderr)
    script = 'display notification "Lista de ofertas lista. Revisa digest.md" with title "Ofertas"'
    if shutil.which("osascript"):
        subprocess.run(["osascript", "-e", script], check=False, timeout=10)


def score(item, max_price):
    """Mejor oferta: más descuento respecto al precio máximo y sin palabras de alerta."""
    discount = 1 - parse_price(item["price"]) / max_price
    return discount - 1 if item["flags"] else discount


def best_offers(items, products, limit):
    """Las `limit` mejores ofertas nuevas de cada producto."""
    max_price = {p["name"]: p["max_price"] for p in products}
    best = []
    for product in products:
        new = [i for i in items if i["new"] and i["product"] == product["name"]]
        ranked = sorted(new, key=lambda i: score(i, max_price[i["product"]]), reverse=True)
        best.extend(ranked[:limit])
    return best


def build_offer_message(item):
    """Mensaje para una sola oferta: producto, precio, título, descripción, alertas y enlace."""
    short = " ".join(item["description"].split())[:140] or "Sin descripción"
    lines = [f"🆕 {item['product']} · {item['site']} · {item['price']}", item["title"], short]
    if item["flags"]:
        lines.append(f"⚠️ revisar: {', '.join(item['flags'])}")
    lines.append(item["url"])
    return "\n".join(lines) + "\n"


def build_summary(items, errors, today):
    """Lista agrupada por producto: todas las ofertas encontradas, con las nuevas marcadas."""
    lines = [f"Ofertas {today}"]
    by_product = {}
    for item in items:
        by_product.setdefault(item["product"], []).append(item)
    if not items:
        lines += ["", "Sin ofertas por debajo del precio máximo."]
    for product, product_items in by_product.items():
        lines += ["", f"🛒 {product} ({len(product_items)})"]
        for item in product_items:
            tag = "🆕 " if item["new"] else ""
            lines.append(f"{tag}{item['price']} · {item['site']} · {item['title']}")
            lines.append(item["url"])
            if item["new"]:
                short = " ".join(item["description"].split())[:140]
                if short:
                    lines.append(short)
                if item["flags"]:
                    lines.append(f"⚠️ revisar: {', '.join(item['flags'])}")
    if errors:
        lines += ["", "Errores:"] + [f"- {e}" for e in errors]
    return "\n".join(lines) + "\n"


def main():
    config = load_json(CONFIG_PATH, None)
    if config is None:
        sys.exit(f"No encuentro la configuración en {CONFIG_PATH}")

    seen = set(load_json(SEEN_PATH, []))
    items = []
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="es-ES", viewport={"width": 1280, "height": 900})
        page = context.new_page()

        run_chollos(page, config, seen, errors)  # primero: envía cada chollo al terminar su término

        for product in config["products"]:
            for site, fn, domain in (
                ("Vinted", search_vinted, config["vinted_domain"]),
                ("Wallapop", search_wallapop, config["wallapop_domain"]),
            ):
                try:
                    results = fn(page, domain, product["query"], product["max_price"])
                except Exception as exc:  # una web caída no debe parar el resto
                    errors.append(f"{site} / {product['name']}: {str(exc)[:150]}")
                    pause(config)
                    continue
                for item in results:
                    key = f"{item['site']}:{item['id']}"
                    is_new = key not in seen
                    description, flags = "", []
                    if is_new:
                        seen.add(key)
                        description = fetch_description(page, item["url"])
                        pause(config)
                        flags = review_description(f"{item['title']} {description}", config["red_flags"])
                    found = {**item, "product": product["name"], "new": is_new,
                             "description": description, "flags": flags}
                    items.append(found)
                pause(config)

        browser.close()

    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=1)

    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = build_summary(items, errors, today)
    with open(DIGEST_PATH, "a", encoding="utf-8") as f:
        f.write(summary + "\n")

    top = best_offers(items, config["products"], MAX_OFFERS_PER_PRODUCT)
    if top:
        message = f"Mejores ofertas nuevas {today}\n\n" + "\n".join(build_offer_message(i) for i in top)
    else:
        message = f"Sin ofertas nuevas {today}\n"
    send_summary(message)
    print(summary)


if __name__ == "__main__":
    main()
