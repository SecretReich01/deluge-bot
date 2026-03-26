"""
DelugeRPG Triple Stat Market Monitor - Discord Bot
====================================================
Monitors the DelugeRPG market for Pokemon with all 3 stats (+atk +def +spe)
and pings you on Discord when one appears.

RAILWAY SETUP:
1. Upload this file + requirements.txt to GitHub
2. Deploy on Railway → add these Environment Variables:
   DISCORD_TOKEN   = your bot token
   CHANNEL_ID      = your channel id
   DISCORD_USER_ID = your user id
   DELUGE_USERNAME = your deluge username
   DELUGE_PASSWORD = your deluge password
3. Set Start Command: python deluge_triple_stat_bot.py
"""

import discord
import asyncio
import requests
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

MARKET_URL = "https://www.delugerpg.com/market?search=%40atkdefspe"
LOGIN_URL  = "https://www.delugerpg.com/login"
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
            )
        })
        self._logged_in = False

    def login(self):
        try:
            r    = self.session.get(LOGIN_URL, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            payload = {"username": self.username, "password": self.password}
            form = soup.find("form")
            if form:
                for hidden in form.find_all("input", {"type": "hidden"}):
                    name  = hidden.get("name")
                    value = hidden.get("value", "")
                    if name:
                        payload[name] = value
            resp = self.session.post(LOGIN_URL, data=payload, timeout=15, allow_redirects=True)
            if self.username.lower() in resp.text.lower():
                log.info("Logged in to DelugeRPG as '%s'", self.username)
            else:
                log.warning("Login response did not confirm username — proceeding anyway.")
            self._logged_in = True
            return True
        except Exception as e:
            log.error("Login error: %s", e)
            return False

    def fetch_triple_stat_listings(self):
        if not self._logged_in:
            self.login()
        try:
            r = self.session.get(MARKET_URL, timeout=20)
            r.raise_for_status()
        except Exception as e:
            log.error("Failed to fetch market page: %s", e)
            return []

        soup     = BeautifulSoup(r.text, "html.parser")
        listings = []
        rows = soup.select("tr, .market-item, .offer-row, .pokemon-offer")
        if not rows:
            rows = soup.find_all("tr")

        for row in rows:
            text = row.get_text(" ", strip=True)
            low  = text.lower()
            if not ("+atk" in low and "+def" in low and "+spe" in low):
                continue
            pokemon_name = _extract_pokemon_name(row, text)
            seller       = _extract_seller(row, text)
            link_tag     = row.find("a", href=True)
            detail_url   = ""
            if link_tag:
                href = link_tag["href"]
                detail_url = href if href.startswith("http") else "https://www.delugerpg.com" + href
            listings.append({
                "pokemon": pokemon_name,
                "seller":  seller,
                "stats":   "+atk +def +spe",
                "url":     detail_url,
                "raw":     text[:200],
            })

        log.info("Found %d triple-stat listing(s).", len(listings))
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
            if token.lower() not in {"the", "and", "for", "has", "with", "your"}:
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


async def monitor_market(deluge):
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        log.error("Could not find channel ID %s", CHANNEL_ID)
        return
    log.info("Market monitor started. Checking every %ds.", CHECK_INTERVAL)

    while not client.is_closed():
        try:
            listings     = deluge.fetch_triple_stat_listings()
            new_listings = [l for l in listings if listing_key(l) not in alerted_keys]
            for listing in new_listings:
                key = listing_key(listing)
                alerted_keys.add(key)
                url_line = f"\n🔗 {listing['url']}" if listing["url"] else ""
                message  = (
                    f"<@{DISCORD_USER_ID}> **Triple Stat Pokémon in Market!** 🎉\n"
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
    client.loop.create_task(monitor_market(deluge))


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if message.content.lower() == "!status":
        await message.channel.send(
            f"✅ Bot is running! Checking every **{CHECK_INTERVAL}s**.\n"
            f"Alerts sent so far: **{len(alerted_keys)}**"
        )
    elif message.content.lower() == "!clearcache":
        alerted_keys.clear()
        await message.channel.send("🗑️ Alert cache cleared.")
    elif message.content.lower() == "!help":
        await message.channel.send(
            "**DelugeRPG Triple Stat Bot**\n"
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
