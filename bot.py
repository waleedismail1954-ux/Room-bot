import asyncio
import os
import sqlite3
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from telegram import Bot
from telegram.constants import ParseMode

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
DB_FILE = "seen_ads.db"

# قائمة بالكلمات التي يجب تجاهلها (لأنها ليست غرفاً مفردة)
BAD_WORDS = ["stanovanje", "apartma", "apartment", "studio", "garsonjera", "hiša", "house"]

def is_valid_room(title):
    """التأكد من أن الإعلان هو غرفة وليس شقة أو منزلاً"""
    title_lower = title.lower()
    for word in BAD_WORDS:
        if word in title_lower:
            return False
    return True

async def scrape_nepremicnine(page):
    ads = []
    url = "https://www.nepremicnine.net/oglasi-oddaja/ljubljana-mesto/soba/"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("div.oglas_container", timeout=30000)
        for listing in await page.query_selector_all("div.oglas_container"):
            link_el = await listing.query_selector("a")
            if not link_el: continue
            href = await link_el.get_attribute("href")
            title_el = await listing.query_selector("span.title")
            title = await title_el.inner_text() if title_el else "Soba"
            if href and is_valid_room(title):
                ads.append({
                    "title": title.strip(),
                    "link": urljoin("https://www.nepremicnine.net", href),
                    "source": "Nepremicnine",
                })
    except Exception as e:
        print(f"[Nepremicnine] {e}")
    return ads

async def scrape_kamrica(page):
    ads = []
    url = "https://kamrica.dostop.si/properties/enoposteljna-soba/ljubljana/"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("a[href*='/properties/']", timeout=30000)
        for link_el in await page.query_selector_all("a[href*='/properties/']"):
            href = await link_el.get_attribute("href")
            if href and href.count("/") > 3:
                title = await link_el.inner_text()
                if is_valid_room(title):
                    ads.append({
                        "title": title.strip() or "غرفة في Kamrica",
                        "link": urljoin("https://kamrica.dostop.si", href),
                        "source": "Kamrica",
                    })
    except Exception as e:
        print(f"[Kamrica] {e}")
    return ads

async def scrape_housinganywhere(page):
    ads = []
    # تم تعديل الرابط ليبحث فقط عن غرف (room)
    url = "https://housinganywhere.com/s/Ljubljana--Slovenia/room"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("a[href*='/room/']", timeout=30000)
        seen = set()
        for link_el in await page.query_selector_all("a[href*='/room/']"):
            href = await link_el.get_attribute("href")
            if href and href not in seen:
                seen.add(href)
                title = await link_el.inner_text()
                if is_valid_room(title):
                    ads.append({
                        "title": (title.strip()[:80] or "غرفة في HousingAnywhere"),
                        "link": urljoin("https://housinganywhere.com", href),
                        "source": "HousingAnywhere",
                    })
    except Exception as e:
        print(f"[HousingAnywhere] {e}")
    return ads

async def scrape_bolha(page):
    ads = []
    url = "https://www.bolha.com/oddaja-sob"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("a[href*='/oglas/']", timeout=30000)
        seen = set()
        for link_el in await page.query_selector_all("a[href*='/oglas/']"):
            href = await link_el.get_attribute("href")
            if href and href not in seen:
                seen.add(href)
                title = await link_el.inner_text()
                if is_valid_room(title) and title.strip():
                    ads.append({
                        "title": (title.strip()[:80] or "غرفة في Bolha"),
                        "link": urljoin("https://www.bolha.com", href),
                        "source": "Bolha",
                    })
    except Exception as e:
        print(f"[Bolha] {e}")
    return ads

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            link TEXT PRIMARY KEY,
            source TEXT,
            sent_at TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def is_new_ad(link):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM ads WHERE link = ?", (link,))
    res = cur.fetchone()
    conn.close()
    return res is None

def mark_ad_as_sent(link, source):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "INSERT OR REPLACE INTO ads (link, source, sent_at) VALUES (?, ?, ?)",
        (link, source, datetime.now()),
    )
    conn.commit()
    conn.close()

async def main():
    init_db()
    bot = Bot(token=TELEGRAM_TOKEN)
    print(f"[{datetime.now()}] دورة جديدة...")

    all_ads = []
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        results = await asyncio.gather(
            scrape_nepremicnine(page),
            scrape_kamrica(page),
            scrape_housinganywhere(page),
            scrape_bolha(page),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, list):
                all_ads.extend(r)
            else:
                print(f"مصدر فشل: {r}")
        await browser.close()

    print(f"إجمالي الإعلانات: {len(all_ads)}")
    new_count = 0
    for ad in all_ads:
        if is_new_ad(ad["link"]):
            msg = (
                f"<b>🏠 إعلان غرفة جديد ({ad['source']})</b>\n\n"
                f"<b>العنوان:</b> {ad['title']}\n"
                f"<a href='{ad['link']}'>🔗 اضغط لعرض الإعلان</a>"
            )
            try:
                await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode=ParseMode.HTML)
                mark_ad_as_sent(ad["link"], ad["source"])
                new_count += 1
                await asyncio.sleep(1.5)
            except Exception as e:
                print(f"فشل إرسال: {e}")

    print(f"تم إرسال {new_count} إعلاناً جديداً.")

if __name__ == "__main__":
    asyncio.run(main())
