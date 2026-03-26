"""
DelugeRPG Triple Stat Trade Shop Monitor - Discord Bot
=======================================================
Uses browser cookies to bypass Cloudflare protection.
Monitors trade shop for Pokemon with all 3 stats (+atk +def +spe).
"""

import discord
import asyncio
import requests
from bs4 import BeautifulSoup
import logging
import os

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
DISCORD_TOKEN   = os.environ.get("DISCORD_TOKEN", "")
DISCORD_USER_ID = int(os.environ.get("DISCORD_USER_ID", "0"))
CHANNEL_ID      = int(os.environ.get("CHANNEL_ID", "0"))
DELUGE_USERNAME = os.environ.get("DELUGE_USERNAME", "")
DELUGE_PASSWORD = os.environ.get("DELUGE_PASSWORD", "")

# Browser cookies to bypass Cloudflare
CF_CLEARANCE    = os.environ.get("CF_CLEARANCE", "")
PHPSESSID       = os.environ.get("PHPSESSID", "")

CHECK_INTERVAL = 60

TRADE_URL  = "https://www.delugerpg.com/trade/lookup"
LOGIN_URL  = "https://www.delugerpg.com/login"
BASE_URL   = "https://www.delugerpg.com"
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("TripleStatBot")


class DelugeSession:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.session  = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.delugerpg.com/",
        })
        self._logged_in = False

    def inject_cookies(self):
        """Inject browser cookies to bypass Cloudflare."""
        if CF_CLEARANCE:
            self.session.cookies.set("cf_clearance", CF_CLEARANCE, domain=".delugerpg.com")
            log.info("Injected cf_clearance cookie")
        if PHPSESSID:
            self.session.cookies.set("PHPSESSID", PHPSESSID, domain=".delugerpg.com")
            log.info("Injected PHPSESSID cookie")

    def login(self):
        """Inject cookies first, then try login if needed."""
        self.inject_cookies()

        try:
            # First test if cookies already give us a logged-in session
            log.info("Testing if cookies provide a valid session...")
            r = self.session.get(BASE_URL, timeout=30)
            log.info("Homepage status: %d, length: %d", r.status_code, len(r.text))

            if r.status_code == 403 or "Just a moment" in r.text:
                log.error("Still blocked by Cloudflare — cookies may be expired")
                return False

            if self.username.lower() in r.text.lower() or "logout" in r.text.lower():
                log.info("✅ Already logged in via cookies!")
                self._logged_in = True
                return True

            # Cookies got past Cloudflare but not logged in — do login
            log.info("Past Cloudflare, but not logged in. Attempting login...")
            r = self.session.get(LOGIN_URL, timeout=30)
            log.info("Login page status: %d, length: %d", r.status_code, len(r.text))

            if r.status_code == 403:
                log.error("Login page blocked (403)")
                return False

            soup = BeautifulSoup(r.text, "html.parser")
            payload = {"username": self.username, "password": self.password}

            form = soup.find("form")
            if form:
                for hidden in form.find_all("input", {"type": "hidden"}):
                    name  = hidden.get("name")
                    value = hidden.get("value", "")
                    if name:
                        payload[name] = value
                        log.info("Hidden field: %s", name)

            resp = self.session.post(LOGIN_URL, data=payload, timeout=30, allow_redirects=True)
            log.info("Login POST status: %d, url: %s", resp.status_code, resp.url)

            if self.username.lower() in resp.text.lower() or "logout" in resp.text.lower():
                log.info("✅ Logged in as '%s'", self.username)
                self._logged_in = True

                # Save the new PHPSESSID for next time
                new_sess = self.session.cookies.get("PHPSESSID")
                if new_sess:
                    log.info("New PHPSESSID obtained: %s...", new_sess[:20])
                return True
            else:
                log.warning("Login uncertain — proceeding anyway")
                self._logged_in = True
                return True

        except Exception as e:
            log.error("Login error: %s", e)
            return False

    def fetch_triple_stat_trades(self):
        if not self._logged_in:
            if not self.login():
                log.error("Cannot fetch trades — login failed")
                return []

        try:
            log.info("Fetching trade page...")
            r = self.session.get(TRADE_URL, timeout=30)
            log.info("Trade page status: %d, length: %d", r.status_code, len(r.text))

            if r.status_code == 403 or "Just a moment" in r.text:
                log.warning("Cloudflare blocked trade page — cookies expired?")
                self._logged_in = False
                return []

            if "/login" in r.url:
                log.warning("Redirected to login — session expired")
                self._logged_in = False
                self.login()
                return []

            r.raise_for_status()

        except Exception as e:
            log.error("Failed to fetch trade page: %s", e)
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        # Debug: log page title
        title = soup.find("title")
        log.info("Page title: %s", title.get_text(strip=True) if title else "No title")

        listings = []
        rows = soup.select("tr, .trade-item, .trade-row, .pokemon-trade")
        if not rows:
            rows = soup.find_all("tr")

        log.info("Found %d rows to scan", len(rows))

        for row in rows:
            text = row.get_text(" ", strip=True)
            low  = text.lower()

            if not ("+atk" in low and "+def" in low and "+spe" in low):
                continue

            pokemon_name = _extract_pokemon_name(row, text)
            seller       = _extract_seller(row, text)

            link_tag   = row.find("a", href=True)
            detail_url = ""
            if link_tag:
                href = link_tag["href"]
                detail_url = href if href.startswith("http") else BASE_URL + href

            listings.append({
                "pokemon": pokemon_name,
                "seller":  seller,
                "stats":   "+atk +def +spe",
                "url":     detail_url,
                "raw":     text[:200],
            })

        log.info("Found %d triple-stat trade(s).", len(listings))
        return listings


def _extract_pokemon_name(row, text):
    img = row.find("img", alt=True)
    if img and img["alt"] and len(img["alt"]) < 60:
        return img["alt"].strip()
    for sel in [".pokemon-name", ".poke-name", "td.name", ".name"]:
        el = row.select_one(sel)
        if el:
            return el.get_text(strip=True)
    for token in text.split():
        if token and token[0].isupper() and token.isalpha() and len(token) > 2:
            if token.lower() not in {"the", "and", "for", "has", "with", "your", "trade"}:
                return token
    return "Unknown Pokemon"


def _extract_seller(row, text):
    for sel in [".seller", ".username", ".trainer-name", "td.user"]:
        el = row.select_one(sel)
        if el:
            return el.get_text(strip=True)
    for a in row.find_all("a", href=True):
        href = a["href"]
        if "/trainer/" in href or "/user/" in href or "/profile/" in href:
            return a.get_text(strip=True)
    return "Unknown Trainer"


# ─────────────────────────────────────────────
#  Discord Bot
# ─────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
client  = discord.Client(intents=intents)
alerted_keys: set = set()


def listing_key(listing):
    return listing.get("url") or listing.get("raw", "")[:100]


async def monitor_trades(deluge):
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(CHANNEL_ID)
        except Exception as e:
            log.error("Failed to get channel %s: %s", CHANNEL_ID, e)
            return

    log.info("Trade monitor started — checking every %ds", CHECK_INTERVAL)

    while not client.is_closed():
        try:
            listings     = deluge.fetch_triple_stat_trades()
            new_listings = [l for l in listings if listing_key(l) not in alerted_keys]

            for listing in new_listings:
                key = listing_key(listing)
                alerted_keys.add(key)
                url_line = f"\n🔗 {listing['url']}" if listing["url"] else ""
                message  = (
                    f"@everyone **Triple Stat Pokémon in Trade Shop!** 🎉\n"
                    f"```\n"
                    f"Pokemon  : {listing['pokemon']}\n"
                    f"Stats    : {listing['stats']}\n"
                    f"Seller   : {listing['seller']}\n"
                    f"```"
                    f"{url_line}"
                )
                await channel.send(message)
                log.info("Alert sent for: %s by %s", listing["pokemon"], listing["seller"])

            if not listings:
                log.info("No triple-stat trades found this cycle")

        except Exception as e:
            log.error("Scan error: %s", e)

        await asyncio.sleep(CHECK_INTERVAL)


@client.event
async def on_ready():
    log.info("Discord bot online as %s", client.user)
    deluge = DelugeSession(DELUGE_USERNAME, DELUGE_PASSWORD)
    if deluge.login():
        log.info("Session ready — starting monitor")
    else:
        log.error("⚠️ Login failed — bot will retry on first scan")
    client.loop.create_task(monitor_trades(deluge))


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if message.content.lower() == "!status":
        await message.channel.send(
            f"✅ Bot is running! Checking trade shop every **{CHECK_INTERVAL}s**.\n"
            f"Alerts sent so far: **{len(alerted_keys)}**"
        )
    elif message.content.lower() == "!clearcache":
        alerted_keys.clear()
        await message.channel.send("🗑️ Alert cache cleared.")
    elif message.content.lower() == "!help":
        await message.channel.send(
            "**DelugeRPG Triple Stat Trade Bot**\n"
            "`!status`     — Show bot status\n"
            "`!clearcache` — Clear seen listings cache\n"
            "`!help`       — Show this message"
        )


if __name__ == "__main__":
    missing = []
    if not DISCORD_TOKEN:   missing.append("DISCORD_TOKEN")
    if not CHANNEL_ID:      missing.append("CHANNEL_ID")
    if not CF_CLEARANCE:    missing.append("CF_CLEARANCE")
    if not PHPSESSID:       missing.append("PHPSESSID")
    if missing:
        print(f"❌ Missing env vars: {', '.join(missing)}")
    else:
        log.info("Starting bot...")
        client.run(DISCORD_TOKEN)
