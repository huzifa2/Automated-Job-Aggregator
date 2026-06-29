"""
Smartling Auto-Accept Bot v3
============================
يفتح صفحة الوظائف كل X ثواني ويقبل أي وظيفة متاحة تلقائياً.

الخطوات:
  1. تسجيل الدخول مرة واحدة وحفظ الجلسة
  2. كل X ثواني: فتح صفحة AVAILABLE_TO_ACCEPT
  3. لو في زرار Accept → اضغطه
  4. في الـ popup → اضغط checkbox تحديد الكل
  5. اضغط زر "Accept X strings"
  6. كرر لكل الوظائف الموجودة

المتطلبات:
  pip install playwright python-dotenv
  playwright install chromium
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeout

# ─────────────────────────────────────────────
# إعداد
# ─────────────────────────────────────────────
load_dotenv()

EMAIL          = os.getenv("SMARTLING_EMAIL", "")
PASSWORD       = os.getenv("SMARTLING_PASSWORD", "")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "10"))

SESSION_FILE = Path("smartling_session.json")
LOG_FILE     = Path("smartling_bot.log")
JOBS_URL     = "https://dashboard.smartling.com/app/account-jobs/?filter=AVAILABLE_TO_ACCEPT"
LOGIN_URL    = "https://dashboard.smartling.com/app/login"

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# حفظ واسترجاع الجلسة
# ─────────────────────────────────────────────
async def save_session(context: BrowserContext):
    state = await context.storage_state()
    SESSION_FILE.write_text(json.dumps(state, ensure_ascii=False))
    log.info("💾 تم حفظ الجلسة.")


async def make_context(playwright, use_saved=True):
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    if use_saved and SESSION_FILE.exists():
        context = await browser.new_context(storage_state=SESSION_FILE.read_text())
        log.info("📂 تم تحميل الجلسة المحفوظة.")
    else:
        context = await browser.new_context()
    return browser, context


# ─────────────────────────────────────────────
# تسجيل الدخول
# ─────────────────────────────────────────────
async def login(page: Page) -> bool:
    log.info("🔐 تسجيل الدخول...")
    try:
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(1)

        # إيميل
        await page.fill('input[type="email"]', EMAIL)
        await asyncio.sleep(0.3)

        # كلمة المرور
        await page.fill('input[type="password"]', PASSWORD)
        await asyncio.sleep(0.3)

        # زر الدخول
        await page.click('button[type="submit"]')
        await page.wait_for_url("**/app/**", timeout=20000)

        log.info("✅ تسجيل الدخول نجح.")
        return True

    except Exception as e:
        log.error(f"❌ فشل تسجيل الدخول: {e}")
        await page.screenshot(path="login_error.png")
        return False


# ─────────────────────────────────────────────
# التحقق من تسجيل الدخول
# ─────────────────────────────────────────────
async def is_logged_in(page: Page) -> bool:
    try:
        await page.goto(JOBS_URL, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1)
        return "login" not in page.url and "signin" not in page.url
    except Exception:
        return False


# ─────────────────────────────────────────────
# قبول وظيفة واحدة
# ─────────────────────────────────────────────
async def accept_one_job(page: Page) -> bool:
    """
    يبحث عن أول زرار Accept في الصفحة ويقبل الوظيفة.
    يرجع True لو نجح، False لو فشل أو مفيش وظايف.
    """
    try:
        # ─── ابحث عن زرار Accept ──────────────────────────
        accept_btn = page.locator('button:has-text("Accept")').first
        count = await accept_btn.count()

        if count == 0:
            return False  # مفيش وظايف

        job_name = "مجهولة"
        try:
            row = page.locator('tr, [class*="row"], li').filter(has=accept_btn)
            job_name = await row.locator('[class*="name"], [class*="title"], a').first.inner_text(timeout=2000)
        except Exception:
            pass

        log.info(f"🎯 وظيفة متاحة: {job_name.strip()}")

        # ─── اضغط Accept ──────────────────────────────────
        await accept_btn.click()
        log.info("👆 تم الضغط على Accept")

        # ─── انتظر الـ popup ───────────────────────────────
        try:
            await page.wait_for_selector(
                '[role="dialog"], [class*="modal"], [class*="Modal"]',
                timeout=5000
            )
            log.info("📦 ظهر الـ popup")
        except PlaywrightTimeout:
            log.warning("⚠️ لم يظهر popup — قد تمت العملية مباشرة")
            return True

        await asyncio.sleep(0.5)

        # ─── تحديد الكل ───────────────────────────────────
        # الـ checkbox في header الجدول لتحديد الكل
        select_all = page.locator(
            '[role="dialog"] thead input[type="checkbox"], '
            '[role="dialog"] [class*="header"] input[type="checkbox"], '
            '[class*="modal"] thead input[type="checkbox"], '
            '[class*="Modal"] thead input[type="checkbox"]'
        ).first

        if await select_all.count() > 0:
            is_checked = await select_all.is_checked()
            if not is_checked:
                await select_all.click()
                log.info("☑️ تم تحديد الكل")
                await asyncio.sleep(0.5)
        else:
            # بديل: حدد كل checkboxes في الـ popup
            checkboxes = page.locator(
                '[role="dialog"] input[type="checkbox"], '
                '[class*="modal"] input[type="checkbox"]'
            )
            cb_count = await checkboxes.count()
            for i in range(cb_count):
                cb = checkboxes.nth(i)
                if not await cb.is_checked():
                    await cb.click()
            if cb_count > 0:
                log.info(f"☑️ تم تحديد {cb_count} عنصر")
            await asyncio.sleep(0.5)

        # ─── اضغط زر Accept X strings ─────────────────────
        # الزرار بيكون نصه "Accept X strings" مش "Cancel"
        confirm_btn = page.locator(
            '[role="dialog"] button:not([class*="cancel"]):not([class*="Cancel"]):not(:has-text("Cancel"))'
        ).filter(has_text="Accept").first

        # لو مش لاقيه بالطريقة دي، جرب تاني
        if await confirm_btn.count() == 0:
            confirm_btn = page.locator(
                '[role="dialog"] button, [class*="modal"] button, [class*="Modal"] button'
            ).filter(has_text="Accept").last

        if await confirm_btn.count() > 0:
            btn_text = await confirm_btn.inner_text()
            log.info(f"✅ ضغط زر: {btn_text.strip()}")
            await confirm_btn.click()
            await asyncio.sleep(2)
            return True
        else:
            log.error("❌ لم يُعثر على زر التأكيد في الـ popup")
            await page.screenshot(path=f"popup_debug.png")
            # اضغط Escape للإغلاق وتجنب التعليق
            await page.keyboard.press("Escape")
            return False

    except Exception as e:
        log.error(f"❌ خطأ أثناء القبول: {e}")
        await page.screenshot(path=f"error.png")
        return False


# ─────────────────────────────────────────────
# الحلقة الرئيسية
# ─────────────────────────────────────────────
async def monitor_loop():
    if not EMAIL or not PASSWORD:
        log.error("❌ يجب ضبط SMARTLING_EMAIL و SMARTLING_PASSWORD في ملف .env")
        return

    log.info("🚀 Smartling Bot v3 يعمل...")
    log.info(f"⏱️  فحص كل {CHECK_INTERVAL} ثانية")

    accepted_total = 0

    async with async_playwright() as pw:
        browser, context = await make_context(pw, use_saved=True)
        page = await context.new_page()

        # تسجيل الدخول إن لزم
        if not await is_logged_in(page):
            success = await login(page)
            if not success:
                await browser.close()
                return
            await save_session(context)
        else:
            log.info("✅ الجلسة صالحة.")

        try:
            while True:
                now = datetime.now().strftime("%H:%M:%S")
                log.info(f"🔄 [{now}] فحص الوظائف...")

                # إعادة تسجيل الدخول لو انتهت الجلسة
                if "login" in page.url or "signin" in page.url:
                    log.warning("⚠️ انتهت الجلسة! إعادة تسجيل الدخول...")
                    if not await login(page):
                        await asyncio.sleep(60)
                        continue
                    await save_session(context)

                # تحديث الصفحة
                try:
                    await page.goto(JOBS_URL, wait_until="networkidle", timeout=20000)
                    await asyncio.sleep(2)
                except Exception as e:
                    log.warning(f"⚠️ خطأ في تحميل الصفحة: {e}")
                    await asyncio.sleep(10)
                    continue

                # قبول كل الوظائف المتاحة
                jobs_accepted_this_round = 0
                while True:
                    success = await accept_one_job(page)
                    if success:
                        accepted_total += 1
                        jobs_accepted_this_round += 1
                        log.info(f"📊 الإجمالي: {accepted_total} وظيفة")
                        # أعد تحميل الصفحة للبحث عن وظائف أخرى
                        await page.goto(JOBS_URL, wait_until="networkidle", timeout=20000)
                        await asyncio.sleep(1)
                    else:
                        break  # مفيش وظايف أو فشل

                if jobs_accepted_this_round == 0:
                    log.info(f"😴 لا وظائف. انتظار {CHECK_INTERVAL}ث...")

                await asyncio.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("🛑 تم الإيقاف.")
        finally:
            await save_session(context)
            await browser.close()
            log.info(f"📊 الإجمالي النهائي: {accepted_total} وظيفة")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(monitor_loop())
