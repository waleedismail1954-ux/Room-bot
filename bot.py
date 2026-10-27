import asyncio
import os
import sqlite3
import re
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from telegram import Bot
from telegram.constants import ParseMode

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
DB_FILE = "seen_ads.db"

BAD_WORDS = ["stanovanje", "apartma", "apartment", "studio", "garsonjera", "hiša", "house"]

def is_valid_room(title):
    title_lower = title.lower()
    for word in BAD_WORDS:
        if word in title_lower:
            return False
    return True

# دالة تنظيف الروابط: تمسح أي مسافة أو سطر أو رمز مخفي من الرابط
def clean_url(url):
    if not url:
        return ""
    return re.sub(r'\s+', '', url) 

# دالة تنظيف النصوص العادية
def clean_text(text):
    if not text:
        return ""
    return text.replace("\n", " ").replace("\r", " ").strip()

async def scrape_nepremicnine(browser):
    ads = []
    page = await browser.new_page()
    url = "https://www.nepremicnine.net/oglasi-oddaja/ljubljana-mesto/soba/"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("div.oglas_container", timeout=60000)
        for listing in await page.query_selector_all("div.oglas_container"):
            link_el = await listing.query_selector("a")
            if not link_el: continue
            href = clean_url(await link_el.get_attribute("href"))
            title_el = await listing.query_selector("span.title")
            title = clean_text(await title_el.inner_text()) if title_el else "Soba"
            if href and is_valid_room(title):
                ads.append({"title": title, "link": urljoin("https://www.nepremicnine.net", href), "source": "Nepremicnine"})
    except Exception as e:
        print(f"[Nepremicnine] {e}")
    finally:
        await page.close()
    return ads

async def scrape_kamrica(browser):
    ads = []
    page = await browser.new_page()
    url = "https://kamrica.dostop.si/properties/enoposteljna-soba/ljubljana/"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("a[href*='/properties/']", timeout=60000)
        for link_el in await page.query_selector_all("a[href*='/properties/']"):
            href = clean_url(await link_el.get_attribute("href"))
            if href and href.count("/") > 3:
                title = clean_text(await link_el.inner_text()) or "غرفة في Kamrica"
                if is_valid_room(title):
                    ads.append({"title": title, "link": urljoin("https://kamrica.dostop.si", href), "source": "Kamrica"})
    except Exception as e:
        print(f"[Kamrica] {e}")
    finally:
        await page.close()
    return ads

async def scrape_housinganywhere(browser):
    ads = []
    page = await browser.new_page()
    url = "https://housinganywhere.com/s/Ljubljana--Slovenia/room"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("a[href*='/room/']", timeout=60000)
        seen = set()
        for link_el in await page.query_selector_all("a[href*='/room/']"):
            href = clean_url(await link_el.get_attribute("href"))
            if href and href not in seen:
                seen.add(href)
                title = clean_text(await link_el.inner_text())[:80] or "غرفة في HousingAnywhere"
                if is_valid_room(title):
                    ads.append({"title": title, "link": urljoin("https://housinganywhere.com", href), "source": "HousingAnywhere"})
    except Exception as e:
        print(f"[HousingAnywhere] {e}")
    finally:
        await page.close()
    return ads

async def scrape_bolha(browser):
    ads = []
    page = await browser.new_page()
    url = "https://www.bolha.com/oddaja-sob"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("a[href*='/oglas/']", timeout=60000)
        seen = set()
        for link_el in await page.query_selector_all("a[href*='/oglas/']"):
            href = clean_url(await link_el.get_attribute("href"))
            if href and href not in seen:
                seen.add(href)
                title = clean_text(await link_el.inner_text())[:80] or "غرفة في Bolha"
                if is_valid_room(title):
                    ads.append({"title": title, "link": urljoin("https://www.bolha.com", href), "source": "Bolha"})
    except Exception as e:
        print(f"[Bolha] {e}")
    finally:
        await page.close()
    return ads

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS ads (link TEXT PRIMARY KEY, source TEXT, sent_at TIMESTAMP)")
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
    conn.execute("INSERT OR REPLACE INTO ads (link, source, sent_at) VALUES (?, ?, ?)", (link, source, datetime.now()))
    conn.commit()
    conn.close()

async def main():
    init_db()
    bot = Bot(token=TELEGRAM_TOKEN)
    print(f"[{datetime.now()}] دورة جديدة...")

    all_ads = []
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        
        print("جاري فحص Nepremicnine...")
        all_ads.extend(await scrape_nepremicnine(browser))
        print("جاري فحص Kamrica...")
        all_ads.extend(await scrape_kamrica(browser))
        print("جاري فحص HousingAnywhere...")
        all_ads.extend(await scrape_housinganywhere(browser))
        print("جاري فحص Bolha...")
        all_ads.extend(await scrape_bolha(browser))
        
        await browser.close()

    print(f"إجمالي الإعلانات: {len(all_ads)}")
    new_count = 0
    for ad in all_ads:
        if is_new_ad(ad["link"]):
            msg = f"<b>🏠 إعلان غرفة جديد ({ad['source']})</b>\n\n<b>العنوان:</b> {ad['title']}\n<a href='{ad['link']}'>🔗 اضغط لعرض الإعلان</a>"
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
