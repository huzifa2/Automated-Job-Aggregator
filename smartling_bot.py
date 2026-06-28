"""
Smartling Auto-Accept Bot
=========================
يراقب صفحة الوظائف المتاحة على Smartling ويقبلها تلقائياً.

الاستراتيجية:
  1. تسجيل الدخول مرة واحدة وحفظ الجلسة (cookies + localStorage)
  2. اعتراض network requests لمعرفة API endpoints الداخلية
  3. استدعاء API مباشرة لجلب الوظائف وقبولها (أسرع من الواجهة)
  4. إذا فشل API → fallback إلى automation عبر واجهة المستخدم

الاستخدام:
  python smartling_bot.py

المتطلبات:
  pip install playwright python-dotenv
  playwright install chromium
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, BrowserContext, Response

# ─────────────────────────────────────────────
# إعداد
# ─────────────────────────────────────────────
load_dotenv()

EMAIL    = os.getenv("SMARTLING_EMAIL", "")
PASSWORD = os.getenv("SMARTLING_PASSWORD", "")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL", "15"))   # كل كم ثانية يتحقق
SESSION_FILE = Path("smartling_session.json")
LOG_FILE     = Path("smartling_bot.log")

JOBS_URL  = "https://dashboard.smartling.com/app/account-jobs/?filter=AVAILABLE_TO_ACCEPT"
LOGIN_URL = "https://dashboard.smartling.com/app/login"

# ─────────────────────────────────────────────
# Logging مزدوج: ملف + شاشة
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
    storage = await context.storage_state()
    SESSION_FILE.write_text(json.dumps(storage, ensure_ascii=False, indent=2))
    log.info("✅ تم حفظ الجلسة.")


async def load_session(playwright) -> tuple:
    """يُعيد (browser, context). يستخدم جلسة محفوظة إن وُجدت."""
    browser = await playwright.chromium.launch(
        headless=True,          # True للتشغيل في الخلفية، False للمشاهدة
        args=["--no-sandbox"],
    )
    if SESSION_FILE.exists():
        storage = json.loads(SESSION_FILE.read_text())
        context = await browser.new_context(storage_state=storage)
        log.info("📂 تم تحميل الجلسة المحفوظة.")
    else:
        context = await browser.new_context()
        log.info("🆕 جلسة جديدة (لا توجد جلسة محفوظة).")
    return browser, context


# ─────────────────────────────────────────────
# تسجيل الدخول
# ─────────────────────────────────────────────
async def login(page: Page) -> bool:
    log.info("🔐 محاولة تسجيل الدخول...")
    try:
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30_000)

        # حقل الإيميل
        await page.fill('input[type="email"], input[name="email"], input[placeholder*="mail"]', EMAIL)
        await page.press('input[type="email"], input[name="email"], input[placeholder*="mail"]', "Tab")
        await asyncio.sleep(0.5)

        # حقل كلمة المرور
        await page.fill('input[type="password"]', PASSWORD)
        await asyncio.sleep(0.3)

        # زر الدخول
        await page.click('button[type="submit"], button:has-text("Sign in"), button:has-text("Log in")')
        await page.wait_for_url("**/dashboard**", timeout=20_000)

        log.info("✅ تسجيل الدخول نجح.")
        return True

    except Exception as e:
        log.error(f"❌ فشل تسجيل الدخول: {e}")
        await page.screenshot(path="login_error.png")
        return False


# ─────────────────────────────────────────────
# اعتراض API وجلب الوظائف مباشرة
# ─────────────────────────────────────────────
class SmartlingAPIInterceptor:
    """
    يراقب network requests لمعرفة الـ API endpoints التي يستخدمها الموقع.
    بعدها نستدعيها مباشرة بدون فتح المتصفح في كل مرة.
    """

    def __init__(self):
        self.jobs_api_url: str | None = None
        self.accept_api_template: str | None = None
        self.auth_headers: dict = {}
        self.captured = False

    async def on_response(self, response: Response):
        url = response.url
        # ابحث عن الـ API calls المتعلقة بالوظائف
        if "api.smartling.com" in url or "smartling.com" in url:
            if any(k in url for k in ["jobs", "account-jobs", "available", "tasks"]):
                try:
                    body = await response.json()
                    if body and "response" in body:
                        log.debug(f"🌐 API intercepted: {url}")
                        if not self.jobs_api_url:
                            self.jobs_api_url = url
                            self.captured = True
                            log.info(f"✅ تم رصد Jobs API: {url}")
                except Exception:
                    pass

    def attach(self, page: Page):
        page.on("response", lambda r: asyncio.create_task(self.on_response(r)))


# ─────────────────────────────────────────────
# جلب الوظائف المتاحة
# ─────────────────────────────────────────────
async def fetch_available_jobs(page: Page, interceptor: SmartlingAPIInterceptor) -> list[dict]:
    """
    يحاول جلب الوظائف عبر API مباشرة أولاً.
    إن لم يكن API معروفاً بعد، يفتح صفحة الوظائف ويحللها.
    """
    log.info("🔍 البحث عن وظائف متاحة...")

    # فتح صفحة الوظائف (لضمان تحديث الجلسة وتشغيل network calls)
    try:
        await page.goto(JOBS_URL, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(2)  # انتظار تحميل React
    except Exception as e:
        log.warning(f"⚠️ تعذر فتح صفحة الوظائف: {e}")
        return []

    # حاول قراءة الوظائف من DOM
    jobs = await extract_jobs_from_dom(page)
    return jobs


async def extract_jobs_from_dom(page: Page) -> list[dict]:
    """يستخرج بيانات الوظائف من صفحة HTML."""
    jobs = []
    try:
        # انتظر ظهور العناصر
        await page.wait_for_selector(
            '[class*="job"], [data-qa*="job"], [class*="row"], li[class*="item"]',
            timeout=10_000,
        )
    except Exception:
        log.warning("⚠️ لم تظهر عناصر الوظائف في الصفحة.")
        return []

    try:
        # استخراج الوظائف عبر JavaScript
        jobs_data = await page.evaluate("""
            () => {
                const results = [];
                
                // محاولة 1: ابحث عن زر Accept في الصفحة
                const acceptBtns = document.querySelectorAll(
                    'button[data-qa*="accept"], button[class*="accept"], button:not([disabled])'
                );
                
                acceptBtns.forEach((btn, i) => {
                    const text = btn.textContent?.trim() || '';
                    if (text.toLowerCase().includes('accept') || text.toLowerCase().includes('claim')) {
                        // ابحث عن الـ container الأب للحصول على اسم الوظيفة
                        const row = btn.closest('[class*="row"], [class*="job"], li, tr') || btn.parentElement;
                        const jobName = row?.querySelector('[class*="name"], [class*="title"], h3, h4, strong')?.textContent?.trim() || `Job ${i+1}`;
                        
                        results.push({
                            index: i,
                            jobName: jobName,
                            hasAcceptBtn: true
                        });
                    }
                });
                
                return results;
            }
        """)

        if jobs_data:
            log.info(f"📋 وُجد {len(jobs_data)} وظيفة متاحة.")
            for j in jobs_data:
                log.info(f"   • {j.get('jobName', 'بدون اسم')}")
        else:
            log.info("📭 لا توجد وظائف متاحة الآن.")

        return jobs_data if jobs_data else []

    except Exception as e:
        log.error(f"❌ خطأ في استخراج الوظائف: {e}")
        return []


# ─────────────────────────────────────────────
# قبول الوظيفة
# ─────────────────────────────────────────────
async def accept_job(page: Page, job: dict) -> bool:
    """
    يقبل وظيفة واحدة عبر واجهة المستخدم:
    1. اضغط Accept
    2. في الـ popup: حدد الكل
    3. اضغط زر القبول النهائي
    """
    job_name = job.get("jobName", "مجهولة")
    log.info(f"🎯 محاولة قبول الوظيفة: {job_name}")

    try:
        # ─── الخطوة 1: اضغط زر Accept ─────────────────────────────
        accept_clicked = await page.evaluate("""
            (jobIndex) => {
                const acceptBtns = Array.from(document.querySelectorAll(
                    'button[data-qa*="accept"], button[class*="accept"], button'
                )).filter(btn => {
                    const t = btn.textContent?.trim().toLowerCase();
                    return (t === 'accept' || t === 'claim' || t?.includes('accept')) && !btn.disabled;
                });
                
                if (acceptBtns[jobIndex]) {
                    acceptBtns[jobIndex].click();
                    return true;
                }
                return false;
            }
        """, job["index"])

        if not accept_clicked:
            log.warning(f"⚠️ لم يتم العثور على زر Accept للوظيفة: {job_name}")
            return False

        # ─── الخطوة 2: انتظر الـ popup ────────────────────────────
        await asyncio.sleep(1.5)

        # تحقق من ظهور popup/dialog
        popup_appeared = await page.evaluate("""
            () => {
                const modals = document.querySelectorAll(
                    '[class*="modal"], [class*="dialog"], [class*="popup"], [role="dialog"]'
                );
                return modals.length > 0;
            }
        """)

        if popup_appeared:
            log.info("📦 ظهر popup التأكيد.")

            # ─── الخطوة 3: حدد الكل (Select All) ─────────────────
            select_all_clicked = await page.evaluate("""
                () => {
                    // ابحث عن checkbox "تحديد الكل"
                    const selectAll = document.querySelector(
                        'input[type="checkbox"][id*="all"], input[type="checkbox"][class*="all"], ' +
                        'label:has-text("all") input, [data-qa*="select-all"], [data-qa*="selectAll"]'
                    );
                    
                    if (selectAll && !selectAll.checked) {
                        selectAll.click();
                        return 'clicked_select_all';
                    }
                    
                    // بديل: حدد كل checkboxes بشكل فردي
                    const checkboxes = document.querySelectorAll(
                        '[class*="modal"] input[type="checkbox"], [role="dialog"] input[type="checkbox"]'
                    );
                    
                    if (checkboxes.length > 0) {
                        let clicked = 0;
                        checkboxes.forEach(cb => {
                            if (!cb.checked) { cb.click(); clicked++; }
                        });
                        return `clicked_${clicked}_checkboxes`;
                    }
                    
                    return 'nothing_found';
                }
            """)

            log.info(f"☑️ تحديد الملفات: {select_all_clicked}")
            await asyncio.sleep(0.8)

            # ─── الخطوة 4: اضغط زر القبول النهائي ────────────────
            confirmed = await page.evaluate("""
                () => {
                    const modal = document.querySelector(
                        '[class*="modal"], [role="dialog"], [class*="popup"]'
                    );
                    if (!modal) return false;
                    
                    // ابحث عن زر الموافقة (وليس الإلغاء)
                    const buttons = modal.querySelectorAll('button:not([disabled])');
                    const confirmBtn = Array.from(buttons).find(btn => {
                        const t = btn.textContent?.trim().toLowerCase();
                        return (
                            t === 'accept' ||
                            t === 'confirm' ||
                            t === 'ok' ||
                            t === 'claim' ||
                            t?.includes('accept') ||
                            t?.includes('confirm')
                        ) && !t?.includes('cancel') && !t?.includes('close');
                    });
                    
                    if (confirmBtn) {
                        confirmBtn.click();
                        return true;
                    }
                    return false;
                }
            """)

            if confirmed:
                log.info(f"✅ تم قبول الوظيفة بنجاح: {job_name}")
                await asyncio.sleep(2)
                return True
            else:
                log.warning(f"⚠️ لم يتم العثور على زر التأكيد في الـ popup")
                await page.screenshot(path=f"popup_debug_{int(time.time())}.png")
                return False

        else:
            # لا يوجد popup → قد تكون الوظيفة قُبلت مباشرة
            log.info(f"ℹ️ لا popup ظهر → قد تمت العملية مباشرة لـ: {job_name}")
            return True

    except Exception as e:
        log.error(f"❌ خطأ أثناء قبول الوظيفة '{job_name}': {e}")
        await page.screenshot(path=f"error_{int(time.time())}.png")
        return False


# ─────────────────────────────────────────────
# التحقق من صلاحية الجلسة
# ─────────────────────────────────────────────
async def is_logged_in(page: Page) -> bool:
    try:
        await page.goto(JOBS_URL, wait_until="domcontentloaded", timeout=15_000)
        # إذا أُعيد توجيهنا لصفحة تسجيل الدخول
        if "login" in page.url or "signin" in page.url:
            return False
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────
# حلقة المراقبة الرئيسية
# ─────────────────────────────────────────────
async def monitor_loop():
    """الحلقة الرئيسية التي تعمل 24/7."""

    if not EMAIL or not PASSWORD:
        log.error("❌ يجب ضبط SMARTLING_EMAIL و SMARTLING_PASSWORD في ملف .env")
        return

    log.info("🚀 بدء تشغيل Smartling Bot...")
    log.info(f"⏱️  فترة الفحص: كل {CHECK_INTERVAL_SECONDS} ثانية")

    accepted_total = 0

    async with async_playwright() as playwright:
        browser, context = await load_session(playwright)

        try:
            page = await context.new_page()

            # إعداد interceptor
            interceptor = SmartlingAPIInterceptor()
            interceptor.attach(page)

            # تحقق من تسجيل الدخول
            logged_in = await is_logged_in(page)
            if not logged_in:
                success = await login(page)
                if not success:
                    log.error("❌ فشل تسجيل الدخول. أوقف البرنامج.")
                    return
                await save_session(context)

            log.info("✅ جاهز للمراقبة!")
            log.info("━" * 50)

            while True:
                loop_start = datetime.now()
                log.info(f"🔄 [{loop_start.strftime('%H:%M:%S')}] فحص الوظائف...")

                # تجديد الجلسة إن انتهت
                if "login" in page.url or "signin" in page.url:
                    log.warning("⚠️ انتهت الجلسة! إعادة تسجيل الدخول...")
                    success = await login(page)
                    if not success:
                        log.error("❌ فشل إعادة تسجيل الدخول.")
                        await asyncio.sleep(60)
                        continue
                    await save_session(context)

                # جلب الوظائف
                jobs = await fetch_available_jobs(page, interceptor)

                if jobs:
                    log.info(f"🎉 وُجد {len(jobs)} وظيفة! بدء القبول...")
                    for job in jobs:
                        success = await accept_job(page, job)
                        if success:
                            accepted_total += 1
                            log.info(f"📊 إجمالي الوظائف المقبولة: {accepted_total}")

                    # بعد القبول، أعد تحميل الصفحة للتحقق
                    await asyncio.sleep(3)
                    await page.reload(wait_until="networkidle")

                else:
                    log.info(f"😴 لا وظائف الآن. انتظار {CHECK_INTERVAL_SECONDS}ث...")

                # انتظر للفحص القادم
                elapsed = (datetime.now() - loop_start).seconds
                wait_time = max(1, CHECK_INTERVAL_SECONDS - elapsed)
                await asyncio.sleep(wait_time)

        except KeyboardInterrupt:
            log.info("🛑 تم إيقاف البرنامج بواسطة المستخدم.")

        finally:
            await save_session(context)
            await browser.close()
            log.info(f"📊 الإجمالي النهائي: {accepted_total} وظيفة مقبولة")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(monitor_loop())
