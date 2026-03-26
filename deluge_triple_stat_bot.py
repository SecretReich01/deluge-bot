"""
DelugeRPG Triple Stat Trade Shop Monitor - Discord Bot
=======================================================
Monitors the DelugeRPG trade shop for Pokemon with all 3 stats (+atk +def +spe)
and pings you on Discord when one appears.
"""

import discord
import asyncio
import cloudscraper
from bs4 import BeautifulSoup
import logging
import os

# ─────────────────────────────────────────────
#  CONFIG — reads from Railway Environment Variables
# ─────────────────────────────────────────────
DISCORD_TOKEN   = os.environ.get("DISCORD_TOKEN", "")
DISCORD_USER_ID = int(os.environ.get("DISCORD_USER_ID", "0"))
CHANNEL_ID      = int(os.environ.get("CHANNEL_ID", "0"))
DELUGE_USERNAME = os.environ.get("DELUGE_USERNAME", "")
DELUGE_PASSWORD = os.environ.get("DELUGE_PASSWORD", "")

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
        # cloudscraper handles Cloudflare challenges automatically
        self.session  = cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "desktop": True,
            }
        )
        self._logged_in = False
        self._login_attempts = 0

    def login(self):
        self._login_attempts += 1
        try:
            log.info("Login attempt #%d — fetching login page...", self._login_attempts)
            r = self.session.get(LOGIN_URL, timeout=30)
            log.info("Login page status: %d, length: %d", r.status_code, len(r.text))

            # Check if we're stuck on a Cloudflare challenge
            if "Just a moment" in r.text or "cf-challenge" in r.text:
                log.warning("Cloudflare challenge detected on login page!")
                return False

            soup = BeautifulSoup(r.text, "html.parser")
            payload = {"username": self.username, "password": self.password}

            # Grab all hidden form fields (CSRF tokens, etc.)
            form = soup.find("form")
            if form:
                for hidden in form.find_all("input", {"type": "hidden"}):
                    name  = hidden.get("name")
                    value = hidden.get("value", "")
                    if name:
                        payload[name] = value
                        log.info("Found hidden field: %s", name)

            log.info("Submitting login for user '%s'...", self.username)
            resp = self.session.post(LOGIN_URL, data=payload, timeout=30, allow_redirects=True)
            log.info("Login response status: %d, url: %s", resp.status_code, resp.url)

            # Check for successful login
            if self.username.lower() in resp.text.lower():
                log.info("✅ Logged in to DelugeRPG as '%s'", self.username)
                self._logged_in = True
                return True
            elif "logout" in resp.text.lower():
                log.info("✅ Login appears successful (found logout link)")
                self._logged_in = True
                return True
            else:
                log.warning("⚠️ Login may have failed — username not found in response")
                # Log a snippet to help debug
                log.debug("Response snippet: %s", resp.text[:500])
                self._logged_in = True  # Try anyway
                return True

        except Exception as e:
            log.error("❌ Login error: %s", e)
            return False

    def fetch_triple_stat_trades(self):
        if not self._logged_in:
            if not self.login():
                log.error("Cannot fetch trades — login failed")
                return []

        try:
            log.info("Fetching trade page...")
            r = self.session.get(TRADE_URL, timeout=30)
            r.raise_for_status()
            log.info("Trade page status: %d, length: %d", r.status_code, len(r.text))

            # Check for Cloudflare block
            if "Just a moment" in r.text or "cf-challenge" in r.text:
                log.warning("Cloudflare challenge on trade page — re-login needed")
                self._logged_in = False
                return []

            # Check if we got redirected to login
            if "/login" in r.url:
                log.warning("Redirected to login — session expired, re-logging in")
                self._logged_in = False
                self.login()
                return []

        except Exception as e:
            log.error("Failed to fetch trade page: %s", e)
            return []

        soup     = BeautifulSoup(r.text, "html.parser")
        listings = []

        # Log page title for debugging
        title = soup.find("title")
        log.info("Page title: %s", title.get_text(strip=True) if title else "No title")

        # Try common row selectors
        rows = soup.select("tr, .trade-item, .trade-row, .pokemon-trade")
        if not rows:
            rows = soup.find_all("tr")

        log.info("Found %d rows to scan", len(rows))

        for row in rows:
            text = row.get_text(" ", strip=True)
            low  = text.lower()

            # Must have all three stats
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
        log.error("Could not find channel ID %s — trying to fetch...", CHANNEL_ID)
        try:
            channel = await client.fetch_channel(CHANNEL_ID)
        except Exception as e:
            log.error("Failed to fetch channel: %s", e)
            return

    log.info("Trade shop monitor started. Checking every %ds.", CHECK_INTERVAL)

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

        except Exception as e:
            log.error("Scan error: %s", e)

        await asyncio.sleep(CHECK_INTERVAL)


@client.event
async def on_ready():
    log.info("Discord bot online as %s", client.user)
    deluge = DelugeSession(DELUGE_USERNAME, DELUGE_PASSWORD)
    deluge.login()
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
    if not DISCORD_USER_ID: missing.append("DISCORD_USER_ID")
    if not CHANNEL_ID:      missing.append("CHANNEL_ID")
    if not DELUGE_USERNAME: missing.append("DELUGE_USERNAME")
    if not DELUGE_PASSWORD: missing.append("DELUGE_PASSWORD")
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
    else:
        log.info("Starting bot...")
        client.run(DISCORD_TOKEN)
