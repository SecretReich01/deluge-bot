"""
DelugeRPG Triple Stat Trade Shop Monitor - Discord Bot
Uses Playwright (real Chromium browser) to bypass Cloudflare.
"""

import discord
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import logging
import os

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
DISCORD_TOKEN   = os.environ.get("DISCORD_TOKEN", "")
CHANNEL_ID      = int(os.environ.get("CHANNEL_ID", "0"))
DELUGE_USERNAME = os.environ.get("DELUGE_USERNAME", "")
DELUGE_PASSWORD = os.environ.get("DELUGE_PASSWORD", "")

CHECK_INTERVAL = 60

TRADE_URL = "https://www.delugerpg.com/trade/lookup"
LOGIN_URL = "https://www.delugerpg.com/login"
BASE_URL  = "https://www.delugerpg.com"
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("TripleStatBot")


class DelugeSession:
    def __init__(self, username, password):
        self.username  = username
        self.password  = password
        self.pw        = None
        self.browser   = None
        self.context   = None
        self.page      = None
        self._logged_in = False

    async def start(self):
        log.info("Launching Chromium browser...")
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        self.context = await self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        self.page = await self.context.new_page()
        log.info("✅ Browser launched")

    async def wait_for_cloudflare(self, timeout=30):
        """Wait for Cloudflare challenge to auto-solve."""
        for i in range(timeout):
            content = await self.page.content()
            title   = await self.page.title()
            if "just a moment" not in title.lower() and "just a moment" not in content[:500].lower():
                if i > 0:
                    log.info("Cloudflare cleared after %ds", i)
                return True
            if i % 5 == 0:
                log.info("Waiting for Cloudflare... (%ds)", i)
            await asyncio.sleep(1)
        log.error("Cloudflare did not clear after %ds", timeout)
        return False

    async def login(self):
        if not self.browser:
            await self.start()

        try:
            log.info("Navigating to login page...")
            await self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

            if not await self.wait_for_cloudflare():
                return False

            title = await self.page.title()
            log.info("Page title: %s", title)

            # Already logged in?
            content = await self.page.content()
            if "logout" in content.lower():
                log.info("✅ Already logged in!")
                self._logged_in = True
                return True

            # Fill username
            log.info("Filling login form for '%s'...", self.username)
            username_ok = False
            for sel in ['input[name="username"]', "#username", 'input[type="text"]']:
                try:
                    await self.page.fill(sel, self.username, timeout=5000)
                    username_ok = True
                    log.info("Username filled (%s)", sel)
                    break
                except Exception:
                    continue
            if not username_ok:
                log.error("Could not find username field!")
                log.info("Page snippet: %s", content[:800])
                return False

            # Fill password
            password_ok = False
            for sel in ['input[name="password"]', "#password", 'input[type="password"]']:
                try:
                    await self.page.fill(sel, self.password, timeout=5000)
                    password_ok = True
                    log.info("Password filled (%s)", sel)
                    break
                except Exception:
                    continue
            if not password_ok:
                log.error("Could not find password field!")
                return False

            # Submit
            log.info("Submitting login...")
            submitted = False
            for sel in ['button[type="submit"]', 'input[type="submit"]', ".login-btn", "button"]:
                try:
                    await self.page.click(sel, timeout=5000)
                    submitted = True
                    log.info("Clicked submit (%s)", sel)
                    break
                except Exception:
                    continue
            if not submitted:
                await self.page.keyboard.press("Enter")
                log.info("Submitted via Enter key")

            # Wait for navigation
            await self.page.wait_for_load_state("networkidle", timeout=30000)
            await self.wait_for_cloudflare()

            content = await self.page.content()
            url     = self.page.url
            log.info("Post-login URL: %s", url)

            if self.username.lower() in content.lower() or "logout" in content.lower():
                log.info("✅ Login successful!")
                self._logged_in = True
                return True
            else:
                log.warning("Login uncertain — proceeding anyway")
                self._logged_in = True
                return True

        except Exception as e:
            log.error("Login error: %s", e)
            return False

    async def fetch_triple_stat_trades(self):
        if not self._logged_in:
            if not await self.login():
                log.error("Cannot fetch trades — login failed")
                return []

        try:
            log.info("Navigating to trade page...")
            await self.page.goto(TRADE_URL, wait_until="domcontentloaded", timeout=60000)

            if not await self.wait_for_cloudflare():
                self._logged_in = False
                return []

            await asyncio.sleep(2)  # let page render

            content = await self.page.content()
            title   = await self.page.title()
            url     = self.page.url
            log.info("Trade page — title: %s, url: %s, len: %d", title, url, len(content))

            if "/login" in url:
                log.warning("Redirected to login — re-logging in")
                self._logged_in = False
                await self.login()
                return []

        except Exception as e:
            log.error("Failed to load trade page: %s", e)
            return []

        soup     = BeautifulSoup(content, "html.parser")
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

        log.info("Found %d triple-stat trade(s)", len(listings))
        return listings

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()


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
            log.error("Cannot find channel %s: %s", CHANNEL_ID, e)
            return

    log.info("Trade monitor started — checking every %ds", CHECK_INTERVAL)

    while not client.is_closed():
        try:
            listings     = await deluge.fetch_triple_stat_trades()
            new_listings = [l for l in listings if listing_key(l) not in alerted_keys]

            for listing in new_listings:
                key = listing_key(listing)
                alerted_keys.add(key)
                url_line = f"\n🔗 {listing['url']}" if listing["url"] else ""
                msg = (
                    f"@everyone **Triple Stat Pokémon in Trade Shop!** 🎉\n"
                    f"```\n"
                    f"Pokemon  : {listing['pokemon']}\n"
                    f"Stats    : {listing['stats']}\n"
                    f"Seller   : {listing['seller']}\n"
                    f"```"
                    f"{url_line}"
                )
                await channel.send(msg)
                log.info("Alert sent: %s by %s", listing["pokemon"], listing["seller"])

            if not listings:
                log.info("No triple-stat trades this cycle")

        except Exception as e:
            log.error("Scan error: %s", e)

        await asyncio.sleep(CHECK_INTERVAL)


@client.event
async def on_ready():
    log.info("Discord bot online as %s", client.user)
    deluge = DelugeSession(DELUGE_USERNAME, DELUGE_PASSWORD)
    await deluge.login()
    client.loop.create_task(monitor_trades(deluge))


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    cmd = message.content.lower()
    if cmd == "!status":
        await message.channel.send(
            f"✅ Bot running! Checking every **{CHECK_INTERVAL}s**.\n"
            f"Alerts sent: **{len(alerted_keys)}**"
        )
    elif cmd == "!clearcache":
        alerted_keys.clear()
        await message.channel.send("🗑️ Cache cleared.")
    elif cmd == "!help":
        await message.channel.send(
            "**DelugeRPG Triple Stat Bot**\n"
            "`!status`     — Bot status\n"
            "`!clearcache` — Clear seen listings\n"
            "`!help`       — This message"
        )


if __name__ == "__main__":
    missing = []
    if not DISCORD_TOKEN:   missing.append("DISCORD_TOKEN")
    if not CHANNEL_ID:      missing.append("CHANNEL_ID")
    if not DELUGE_USERNAME: missing.append("DELUGE_USERNAME")
    if not DELUGE_PASSWORD: missing.append("DELUGE_PASSWORD")
    if missing:
        print(f"❌ Missing env vars: {', '.join(missing)}")
    else:
        log.info("Starting bot with Playwright (Chromium)...")
        client.run(DISCORD_TOKEN)
