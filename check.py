#!/usr/bin/env python3
"""
Checks one product page and sends a Discord message when its buy status changes.

Only uses the Python standard library, so no pip install is needed.

Environment variables:
  DISCORD_WEBHOOK  Discord webhook URL (required for notifications)
  PRODUCT_URL      Page to check (optional, defaults to the playmat below)
  STATE_FILE       Where the last known status is stored (default: state.json)
  HTML_FILE        Read HTML from a local file instead of the web (for testing)
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

URL = os.environ.get(
    "PRODUCT_URL",
    "https://amazingtoys.ch/ONE-PIECE-CARD-GAME-Official-Playmat-Limited-Edition-Vol.6",
)
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
HTML_FILE = os.environ.get("HTML_FILE")

# Honest user agent. You can add a contact address here if you like.
USER_AGENT = "personal-stock-checker/1.0 (checks one product page every 10 min)"

FAILURES_BEFORE_ALERT = 3      # alert after this many failed fetches in a row
HEARTBEAT_DAYS = 30            # touch state.json at least this often (keeps GitHub cron alive)

AVAILABILITY_RE = re.compile(
    r"schema\.org/(InStock|OutOfStock|PreOrder|PreSale|SoldOut|BackOrder|"
    r"LimitedAvailability|Discontinued|OnlineOnly|InStoreOnly)",
    re.IGNORECASE,
)
BUYABLE_AVAILABILITY = {"instock", "preorder", "presale", "backorder",
                        "limitedavailability", "onlineonly"}
NOT_BUYABLE_AVAILABILITY = {"outofstock", "soldout", "discontinued", "instoreonly"}
# Class prefix of the main product's buy area. On amazingtoys.ch it carries an
# element id suffix ("product-detail-buy-552bfc..."). "buy-widget" is not used:
# the cross-selling sliders also use <form class="buy-widget"> with a btn-buy.
BUYBOX_CLASS_PREFIX = "product-detail-buy"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}


class ProductPageParser(HTMLParser):
    """Collects the buy button, delivery info text, price and availability."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buttons = []          # list of {"disabled": bool, "text": str}
        self.delivery_parts = []
        self.price = None
        self.availability = []
        self._button = None        # button currently being read
        self._delivery_depth = 0   # >0 while inside a delivery-information element
        self._buybox_depth = 0     # >0 while inside the main product buy area
        self.saw_buybox = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        classes = a.get("class", "")

        # schema.org availability: <link itemprop="availability" href=".../InStock">
        for value in (a.get("href", ""), a.get("content", "")):
            m = AVAILABILITY_RE.search(value)
            if m:
                self.availability.append((m.group(1), self._buybox_depth > 0))

        # Price: <meta property="product:price:amount" content="39.9"> or itemprop="price"
        if tag == "meta" and self.price is None:
            if a.get("property") == "product:price:amount" or a.get("itemprop") == "price":
                self.price = a.get("content") or None

        # Shopware buy button: <button class="btn btn-primary btn-buy" ...>
        if "btn-buy" in classes and tag in ("button", "input", "a"):
            btn = {
                "disabled": "disabled" in a or a.get("aria-disabled") == "true"
                            or " disabled" in f" {classes}",
                "text": a.get("value", "") if tag == "input" else "",
                "title": a.get("title", ""),
                "in_buybox": self._buybox_depth > 0,
            }
            self.buttons.append(btn)
            if tag == "button":
                self._button = btn

        # Track nesting for the delivery text block and the main buy area
        if tag not in VOID_TAGS:
            if self._delivery_depth:
                self._delivery_depth += 1
            elif "delivery-information" in classes:
                self._delivery_depth = 1
            if self._buybox_depth:
                self._buybox_depth += 1
            elif any(c.startswith(BUYBOX_CLASS_PREFIX) for c in classes.split()):
                self._buybox_depth = 1
                self.saw_buybox = True

    def handle_endtag(self, tag):
        if tag == "button":
            self._button = None
        if tag not in VOID_TAGS:
            if self._delivery_depth:
                self._delivery_depth -= 1
            if self._buybox_depth:
                self._buybox_depth -= 1

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._button is not None and len(self._button["text"]) < 60:
            self._button["text"] = (self._button["text"] + " " + text).strip()
        if self._delivery_depth:
            self.delivery_parts.append(text)


def fetch(url):
    if HTML_FILE:
        return Path(HTML_FILE).read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html",
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_status(html):
    p = ProductPageParser()
    p.feed(html)

    # Only trust a button inside the main buy area. Every other btn-buy on the
    # page belongs to a cross-selling product. When sold out, the shop shows a
    # back-in-stock form there instead, so "missing" is the normal sold-out state.
    in_box = [b for b in p.buttons if b["in_buybox"]]
    btn = in_box[0] if in_box else None
    if btn is None:
        button_state = "missing"
    else:
        button_state = "disabled" if btn["disabled"] else "enabled"

    in_box_avail = [v for v, inside in p.availability if inside]
    all_avail = [v for v, _ in p.availability]
    availability = (in_box_avail or all_avail or [None])[0]
    delivery = " ".join(p.delivery_parts)
    delivery = re.sub(r"\s+", " ", delivery)[:200] or None

    avail = (availability or "").lower()
    if avail in NOT_BUYABLE_AVAILABILITY or button_state == "disabled":
        buyable = False
    else:
        buyable = button_state == "enabled" or avail in BUYABLE_AVAILABILITY

    return {
        "buyable": buyable,
        "buy_button": button_state,
        "button_text": ((btn["text"] or btn["title"])[:60] if btn else None) or None,
        "availability": availability,
        "delivery": delivery,
        "price": p.price,
    }


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")


def notify(text):
    print("NOTIFY:", text)
    if not WEBHOOK:
        print("DISCORD_WEBHOOK not set, skipping Discord message")
        return
    body = json.dumps({
        "content": text,
        "allowed_mentions": {"parse": ["everyone"]},
    }).encode("utf-8")
    req = urllib.request.Request(WEBHOOK, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        print("Discord responded", resp.status)


def describe(status):
    lines = [
        f"Buy button: **{status['buy_button']}**"
        + (f" ({status['button_text']})" if status["button_text"] else ""),
        f"Availability: {status['availability'] or 'not found'}",
    ]
    if status["delivery"]:
        lines.append(f"Delivery info: {status['delivery']}")
    if status["price"]:
        lines.append(f"Price: CHF {status['price']}")
    return "\n".join(lines)


def main():
    # Emojis in the log would crash on a Windows console (cp1252) when testing locally
    sys.stdout.reconfigure(encoding="utf-8")
    state = load_state()
    now = datetime.now(timezone.utc)

    try:
        html = fetch(URL)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        failures = state.get("failures", 0) + 1
        state["failures"] = failures
        print(f"Fetch failed ({failures} in a row): {e}")
        if failures == FAILURES_BEFORE_ALERT:
            notify(f"⚠️ Stock checker could not load the page {failures} times in a row.\n"
                   f"Last error: `{e}`\n{URL}")
        save_state(state)
        return 0

    status = extract_status(html)
    print("Current status:", json.dumps(status, ensure_ascii=False))

    previous = state.get("status")
    if state.get("failures", 0) >= FAILURES_BEFORE_ALERT:
        notify("✅ Stock checker can load the page again.")
    state["failures"] = 0

    if previous is None:
        notify("👀 Stock checker is running. Current status:\n" + describe(status) + f"\n{URL}")
    elif status != previous:
        if status["buyable"] and not previous.get("buyable"):
            headline = "@everyone 🟢 **BUYABLE NOW!**"
        elif previous.get("buyable") and not status["buyable"]:
            headline = "🔴 No longer buyable."
        else:
            headline = "🟡 Something changed on the product page."
        changes = [f"{k}: `{previous.get(k)}` → `{status[k]}`"
                   for k in status if previous.get(k) != status[k]]
        notify(f"{headline}\n" + "\n".join(changes) + f"\n{URL}")

    state["status"] = status

    # Write a timestamp now and then so the repo gets a commit at least monthly.
    # GitHub disables scheduled workflows after 60 days without repo activity.
    last = state.get("heartbeat")
    if not last or (now - datetime.fromisoformat(last)).days >= HEARTBEAT_DAYS:
        state["heartbeat"] = now.isoformat(timespec="seconds")

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
