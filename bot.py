import asyncio
import os
import sqlite3
import re
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from telegram import Bot
from telegram.constants import ParseMode

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"].strip()
CHAT_ID = os.environ["CHAT_ID"].strip()
DB_FILE = "seen_ads.db"

BAD_WORDS = ["stanovanje", "apartma", "apartment", "studio", "garsonjera", "hiša", "house"]

def is_valid_room(title):
    title_lower = title.lower()
    for word in BAD_WORDS:
        if word in title_lower:
            return False
    return True

def clean_url(url):
    if not url: return ""
    return re.sub(r'\s+', '', url)

def clean_text(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', text).strip()

async def scrape_nepremicnine(browser):
    ads = []
    page = await browser.new_page()
    url = "https://www.nepremicnine.net/oglasi-oddaja/ljubljana-mesto/soba/"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(5000) 
        listings = await page.query_selector_all("div.oglas_container")
        for listing in listings:
            link_el = await listing.query_selector("a")
            if not link_el: continue
            href = clean_url(await link_el.get_attribute("href"))
            title_el = await listing.query_selector("span.title")
            title = clean_text(await title_el.inner_text()) if title_el else "Soba"
            if href and is_valid_room(title):
                ads.append({"title": title, "link": urljoin("https://www.nepremicnine.net", href), "source": "Nepremicnine"})
    except Exception as e:
        print(f"[Nepremicnine] خطأ: {e}")
    finally:
        await page.close()
    return ads

async def scrape_kamrica(browser):
    ads = []
    page = await browser.new_page()
    url = "https://kamrica.dostop.si/properties/enoposteljna-soba/ljubljana/"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(5000)
        links = await page.query_selector_all("a[href*='/properties/']")
        seen = set()
        for link_el in links:
            href = clean_url(await link_el.get_attribute("href"))
            if href and href not in seen and href.count("/") > 3:
                seen.add(href)
                title = clean_text(await link_el.inner_text()) or "غرفة في Kamrica"
                if is_valid_room(title):
                    ads.append({"title": title, "link": urljoin("https://kamrica.dostop.si", href), "source": "Kamrica"})
    except Exception as e:
        print(f"[Kamrica] خطأ: {e}")
    finally:
        await page.close()
    return ads

async def scrape_housinganywhere(browser):
    ads = []
    page = await browser.new_page()
    url = "https://housinganywhere.com/s/Ljubljana--Slovenia/room"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(5000)
        links = await page.query_selector_all("a[href*='/room/']")
        seen = set()
        for link_el in links:
            href = clean_url(await link_el.get_attribute("href"))
            if href and href not in seen:
                seen.add(href)
                title = clean_text(await link_el.inner_text())[:80] or "غرفة في HousingAnywhere"
                if is_valid_room(title):
                    ads.append({"title": title, "link": urljoin("https://housinganywhere.com", href), "source": "HousingAnywhere"})
    except Exception as e:
        print(f"[HousingAnywhere] خطأ: {e}")
    finally:
        await page.close()
    return ads

async def scrape_bolha(browser):
    ads = []
    page = await browser.new_page()
    url = "https://www.bolha.com/oddaja-sob"
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(5000)
        links = await page.query_selector_all("a[href*='/oglas/']")
        seen = set()
        for link_el in links:
            href = clean_url(await link_el.get_attribute("href"))
            if href and href not in seen:
                seen.add(href)
                title = clean_text(await link_el.inner_text())[:80] or "غرفة في Bolha"
                if is_valid_room(title):
                    ads.append({"title": title, "link": urljoin("https://www.bolha.com", href), "source": "Bolha"})
    except Exception as e:
        print(f"[Bolha] خطأ: {e}")
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
        browser = await p.chromium.launch(headless=True)
        
        print("جاري فحص Nepremicnine...")
        res_nep = await scrape_nepremicnine(browser)
        print(f"--> وجد في Nepremicnine: {len(res_nep)} غرفة")
        all_ads.extend(res_nep)
        
        print("جاري فحص Kamrica...")
        res_kam = await scrape_kamrica(browser)
        print(f"--> وجد في Kamrica: {len(res_kam)} غرفة")
        all_ads.extend(res_kam)
        
        print("جاري فحص HousingAnywhere...")
        res_hou = await scrape_housinganywhere(browser)
        print(f"--> وجد في HousingAnywhere: {len(res_hou)} غرفة")
        all_ads.extend(res_hou)
        
        print("جاري فحص Bolha...")
        res_bol = await scrape_bolha(browser)
        print(f"--> وجد في Bolha: {len(res_bol)} غرفة")
        all_ads.extend(res_bol)
        
        await browser.close()

    print(f"إجمالي الإعلانات المكتشفة: {len(all_ads)}")
    new_count = 0
    
    for ad in all_ads:
        if is_new_ad(ad["link"]):
            msg = f"<b>🏠 إعلان غرفة جديد ({ad['source']})</b>\n\n<b>العنوان:</b> {ad['title']}\n<a href='{ad['link']}'>🔗 اضغط لعرض الإعلان</a>"
            
            success = False
            for attempt in range(3):
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode=ParseMode.HTML, read_timeout=30, write_timeout=30)
                    mark_ad_as_sent(ad["link"], ad["source"])
                    new_count += 1
                    success = True
                    break
                except Exception as e:
                    print(f"فشل إرسال (محاولة {attempt+1}): {e}")
                    await asyncio.sleep(3)
            
            await asyncio.sleep(2)

    print(f"تم إرسال {new_count} إعلاناً جديداً.")

if __name__ == "__main__":
    asyncio.run(main())
