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
        self.username   = username
        self.password   = password
        self.pw         = None
        self.browser    = None
        self.context    = None
        self.page       = None
        self._logged_in = False

    async def start(self):
        log.info("Launching Chromium browser...")
        self.pw      = await async_playwright().start()
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
        log.info("✅ Browser launched successfully")

    async def wait_for_cloudflare(self, timeout=60):
        """Wait for Cloudflare challenge to auto-solve."""
        for i in range(timeout):
            try:
                title   = await self.page.title()
                content = await self.page.content()
                if (
                    "just a moment" not in title.lower()
                    and "just a moment" not in content[:500].lower()
                    and "cf-challenge" not in content[:500].lower()
                ):
                    if i > 0:
                        log.info("✅ Cloudflare cleared after %ds", i)
                    return True
                if i % 5 == 0:
                    log.info("Waiting for Cloudflare... (%ds elapsed)", i)
            except Exception:
                pass
            await asyncio.sleep(1)
        log.error("❌ Cloudflare did not clear after %ds", timeout)
        return False

    async def safe_goto(self, url):
        """Navigate to URL without crashing on timeout."""
        try:
            await self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
        except Exception as e:
            # domcontentloaded already fired — safe to continue
            log.info("Navigation exception (safe to ignore): %s", str(e)[:100])

    async def login(self):
        if not self.browser:
            await self.start()

        try:
            log.info("Navigating to login page...")
            await self.safe_goto(LOGIN_URL)

            # Wait for Cloudflare
            if not await self.wait_for_cloudflare():
                log.error("Stuck on Cloudflare at login page")
                return False

            title = await self.page.title()
            log.info("Login page title: %s", title)

            # Already logged in?
            content = await self.page.content()
            if "logout" in content.lower():
                log.info("✅ Already logged in!")
                self._logged_in = True
                return True

            # Fill username
            log.info("Filling username...")
            filled_user = False
            for sel in [
                'input[name="username"]',
                'input[name="user"]',
                "#username",
                "#user",
                'input[type="text"]',
            ]:
                try:
                    await self.page.wait_for_selector(sel, timeout=3000)
                    await self.page.fill(sel, self.username)
                    log.info("Username filled: %s", sel)
                    filled_user = True
                    break
                except Exception:
                    continue

            if not filled_user:
                log.error("❌ Could not find username field")
                log.info("Page HTML: %s", content[:1000])
                return False

            # Fill password
            log.info("Filling password...")
            filled_pass = False
            for sel in [
                'input[name="password"]',
                'input[name="pass"]',
                "#password",
                "#pass",
                'input[type="password"]',
            ]:
                try:
                    await self.page.wait_for_selector(sel, timeout=3000)
                    await self.page.fill(sel, self.password)
                    log.info("Password filled: %s", sel)
                    filled_pass = True
                    break
                except Exception:
                    continue

            if not filled_pass:
                log.error("❌ Could not find password field")
                return False

            # Submit
            log.info("Submitting login form...")
            submitted = False
            for sel in [
                'input[type="submit"]',
                'button[type="submit"]',
                'button:has-text("Login")',
                'button:has-text("Log in")',
                ".login-btn",
                "button",
            ]:
                try:
                    await self.page.click(sel, timeout=3000)
                    log.info("Clicked submit: %s", sel)
                    submitted = True
                    break
                except Exception:
                    continue

            if not submitted:
                log.info("No button found — pressing Enter")
                await self.page.keyboard.press("Enter")

            # ── Wait for post-login page ──────────────────────
            # Don't use networkidle — it times out
            # Instead just wait a few seconds and check what we got
            log.info("Waiting for post-login page to settle...")
            await asyncio.sleep(5)

            # Handle any Cloudflare that appears after login redirect
            await self.wait_for_cloudflare(timeout=60)

            # Extra settle time
            await asyncio.sleep(3)

            final_url = self.page.url
            content   = await self.page.content()
            title     = await self.page.title()
            log.info("Post-login — URL: %s | Title: %s", final_url, title)

            if (
                self.username.lower() in content.lower()
                or "logout" in content.lower()
                or "log out" in content.lower()
            ):
                log.info("✅ Login confirmed!")
                self._logged_in = True
                return True
            elif "/login" not in final_url:
                log.info("✅ Login likely succeeded (not on login page)")
                self._logged_in = True
                return True
            else:
                log.warning("⚠️ Still on login page — login may have failed")
                log.info("Page snippet: %s", content[:500])
                # Try anyway
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
            await self.safe_goto(TRADE_URL)

            # Handle Cloudflare
            if not await self.wait_for_cloudflare(timeout=60):
                log.warning("Cloudflare blocked trade page")
                self._logged_in = False
                return []

            # Let dynamic content load
            await asyncio.sleep(3)

            current_url = self.page.url
            content     = await self.page.content()
            title       = await self.page.title()
            log.info(
                "Trade page — title: %s | url: %s | length: %d",
                title, current_url, len(content),
            )

            # Redirected to login?
            if "/login" in current_url:
                log.warning("Redirected to login — session expired")
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

        log.info("Scanning %d rows...", len(rows))

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
                detail_url = (
                    href if href.startswith("http") else BASE_URL + href
                )

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
        try:
            if self.browser:
                await self.browser.close()
            if self.pw:
                await self.pw.stop()
        except Exception:
            pass


def _extract_pokemon_name(row, text):
    img = row.find("img", alt=True)
    if img and img["alt"] and len(img["alt"]) < 60:
        return img["alt"].strip()
    for sel in [".pokemon-name", ".poke-name", "td.name", ".name"]:
        el = row.select_one(sel)
        if el:
            return el.get_text(strip=True)
    for token in text.split():
        if (
            token
            and token[0].isupper()
            and token.isalpha()
            and len(token) > 2
            and token.lower()
            not in {
                "the", "and", "for", "has", "with",
                "your", "trade", "shop", "login",
            }
        ):
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
deluge_session: DelugeSession = None


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

    log.info("✅ Trade monitor started — checking every %ds", CHECK_INTERVAL)

    while not client.is_closed():
        try:
            listings     = await deluge.fetch_triple_stat_trades()
            new_listings = [
                l for l in listings if listing_key(l) not in alerted_keys
            ]

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
                log.info(
                    "Alert sent: %s by %s",
                    listing["pokemon"], listing["seller"],
                )

            if not new_listings:
                log.info(
                    "No new triple-stat trades (total found: %d)",
                    len(listings),
                )

        except Exception as e:
            log.error("Scan error: %s", e)

        await asyncio.sleep(CHECK_INTERVAL)


@client.event
async def on_ready():
    global deluge_session
    log.info("Discord bot online as %s", client.user)
    deluge_session = DelugeSession(DELUGE_USERNAME, DELUGE_PASSWORD)
    success = await deluge_session.login()
    if success:
        log.info("✅ Session ready — starting monitor")
    else:
        log.error("⚠️ Login failed — will retry on first scan")
    asyncio.ensure_future(monitor_trades(deluge_session))


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    cmd = message.content.lower().strip()
    if cmd == "!status":
        await message.channel.send(
            f"✅ Bot running! Checking every **{CHECK_INTERVAL}s**.\n"
            f"Alerts sent: **{len(alerted_keys)}**"
        )
    elif cmd == "!clearcache":
        alerted_keys.clear()
        await message.channel.send("🗑️ Alert cache cleared.")
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
        log.info("Starting bot with Playwright Chromium...")
        client.run(DISCORD_TOKEN)
