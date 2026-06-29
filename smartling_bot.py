"""
Smartling Auto-Accept Bot v2
============================
يعمل بدون متصفح — يكلم الـ API مباشرة.

الاستراتيجية:
  1. تسجيل الدخول عبر Playwright مرة واحدة → جلب Cookie + Csrf-Token
  2. حفظ الجلسة
  3. كل X ثواني: استدعاء API الوظائف المتاحة مباشرة
  4. لو في وظيفة → استدعاء API القبول مباشرة (أقل من ثانية)
  5. لو انتهت الجلسة → تسجيل دخول تلقائي

المتطلبات:
  pip install playwright python-dotenv requests
  playwright install chromium
"""

import asyncio
import json
import logging
import os
import time
import requests
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

# ─────────────────────────────────────────────
# إعداد
# ─────────────────────────────────────────────
load_dotenv()

EMAIL             = os.getenv("SMARTLING_EMAIL", "")
PASSWORD          = os.getenv("SMARTLING_PASSWORD", "")
CHECK_INTERVAL    = int(os.getenv("CHECK_INTERVAL", "10"))
LOCALE_ID         = os.getenv("LOCALE_ID", "ar-AE")
WORKFLOW_STEP_UID = os.getenv("WORKFLOW_STEP_UID", "b9043c852bc5")
TARGET_TASK_WORDS = int(os.getenv("TARGET_TASK_WORDS", "2000"))

SESSION_FILE = Path("smartling_session.json")
LOG_FILE     = Path("smartling_bot.log")

LOGIN_URL  = "https://dashboard.smartling.com/app/login"
JOBS_URL   = "https://dashboard.smartling.com/app/account-jobs/?filter=AVAILABLE_TO_ACCEPT"

# API endpoints
API_BASE         = "https://dashboard.smartling.com/p"
API_JOBS_SEARCH  = f"{API_BASE}/jobs-api/v3/accounts/{{account_uid}}/jobs?limit=50&offset=0&sortBy=createdDate&sortDirection=DESC&translationJobStatus=AWAITING_AUTHORIZATIONS,IN_PROGRESS&assignedToCurrentUser=false"
API_CLAIM        = f"{API_BASE}/content-assignments-api/v2/projects/{{project_uid}}/claiming/tasks/create"

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
def save_session(cookie_str: str, csrf_token: str, account_uid: str, project_uid: str):
    data = {
        "cookie": cookie_str,
        "csrf_token": csrf_token,
        "account_uid": account_uid,
        "project_uid": project_uid,
        "saved_at": time.time()
    }
    SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    log.info("✅ تم حفظ الجلسة.")


def load_session():
    if not SESSION_FILE.exists():
        return None
    data = json.loads(SESSION_FILE.read_text())
    # الجلسة صالحة لمدة 12 ساعة
    if time.time() - data.get("saved_at", 0) > 43200:
        log.info("⏰ انتهت صلاحية الجلسة المحفوظة.")
        return None
    log.info("📂 تم تحميل الجلسة المحفوظة.")
    return data


# ─────────────────────────────────────────────
# تسجيل الدخول وجلب الـ tokens
# ─────────────────────────────────────────────
async def do_login() -> dict | None:
    log.info("🔐 تسجيل الدخول...")
    intercepted = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page    = await context.new_page()

        # اعتراض الـ requests لجلب الـ account_uid و project_uid
        async def on_request(request):
            url = request.url
            # جلب الـ account_uid من jobs API
            if "jobs-api" in url and "accounts" in url and "account_uid" not in intercepted:
                import re
                m = re.search(r'/accounts/([^/]+)/', url)
                if m:
                    intercepted["account_uid"] = m.group(1)
                    log.info(f"✅ account_uid: {intercepted['account_uid']}")

            # جلب الـ project_uid من claiming API
            if "content-assignments-api" in url and "projects" in url and "project_uid" not in intercepted:
                import re
                m = re.search(r'/projects/([^/]+)/', url)
                if m:
                    intercepted["project_uid"] = m.group(1)
                    log.info(f"✅ project_uid: {intercepted['project_uid']}")

        page.on("request", lambda r: asyncio.create_task(on_request(r)))

        try:
            # تسجيل الدخول
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(1)

            # إدخال الإيميل
            email_sel = 'input[type="email"], input[name="email"], input[id*="email"], input[placeholder*="mail"]'
            await page.fill(email_sel, EMAIL)
            await asyncio.sleep(0.3)

            # إدخال كلمة المرور
            await page.fill('input[type="password"]', PASSWORD)
            await asyncio.sleep(0.3)

            # الضغط على تسجيل الدخول
            await page.click('button[type="submit"]')
            await page.wait_for_url("**/dashboard**", timeout=20000)
            log.info("✅ تسجيل الدخول نجح.")

            # فتح صفحة الوظائف لاعتراض الـ UIDs
            await page.goto(JOBS_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)

            # جلب الـ cookies
            cookies = await context.cookies()
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies
                                     if "smartling.com" in c.get("domain", "")])

            # جلب الـ CSRF token من الصفحة
            csrf_token = await page.evaluate("""
                () => {
                    // من الـ meta tag
                    const meta = document.querySelector('meta[name="csrf-token"]');
                    if (meta) return meta.content;

                    // من الـ cookies
                    const cookies = document.cookie.split(';');
                    for (const c of cookies) {
                        const [k, v] = c.trim().split('=');
                        if (k.toLowerCase().includes('csrf')) return v;
                    }
                    return null;
                }
            """)

            # جلب الـ CSRF من الـ request headers المعترضة
            if not csrf_token:
                # انتظر request وجلب الـ CSRF منه
                await asyncio.sleep(2)

            log.info(f"🔑 CSRF Token: {csrf_token[:20] if csrf_token else 'لم يُجلب'}...")

            await browser.close()

            if not cookie_str:
                log.error("❌ لم يتم جلب الـ cookies.")
                return None

            return {
                "cookie": cookie_str,
                "csrf_token": csrf_token or "",
                "account_uid": intercepted.get("account_uid", ""),
                "project_uid": intercepted.get("project_uid", "0af7edb35"),  # من الـ API اللي شفناه
            }

        except Exception as e:
            log.error(f"❌ خطأ في تسجيل الدخول: {e}")
            await page.screenshot(path="login_error.png")
            await browser.close()
            return None


# ─────────────────────────────────────────────
# جلب الجلسة (محفوظة أو جديدة)
# ─────────────────────────────────────────────
async def get_session() -> dict | None:
    session = load_session()
    if session:
        # تحقق إن الجلسة لا تزال صالحة
        if test_session(session):
            return session
        log.info("⚠️ الجلسة المحفوظة منتهية، إعادة تسجيل الدخول...")

    session = await do_login()
    if session:
        save_session(
            session["cookie"],
            session["csrf_token"],
            session["account_uid"],
            session["project_uid"]
        )
    return session


def test_session(session: dict) -> bool:
    """تحقق من أن الجلسة لا تزال صالحة."""
    try:
        headers = build_headers(session)
        r = requests.get(
            "https://dashboard.smartling.com/p/preferences-api/v2/preferences/job-searches",
            headers=headers,
            timeout=10
        )
        return r.status_code == 200
    except Exception:
        return False


# ─────────────────────────────────────────────
# بناء الـ Headers
# ─────────────────────────────────────────────
def build_headers(session: dict) -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Cookie": session["cookie"],
        "Csrf-Token": session["csrf_token"],
        "Origin": "https://dashboard.smartling.com",
        "Referer": "https://dashboard.smartling.com/app/account-jobs/?filter=AVAILABLE_TO_ACCEPT",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


# ─────────────────────────────────────────────
# جلب الوظائف المتاحة
# ─────────────────────────────────────────────
def fetch_available_jobs(session: dict) -> list[dict]:
    """يجلب الوظائف المتاحة مباشرة من الـ API."""
    headers = build_headers(session)
    account_uid = session.get("account_uid", "")

    if not account_uid:
        log.warning("⚠️ account_uid غير معروف، سيتم تحديثه عند أول تسجيل دخول.")
        return []

    url = f"https://dashboard.smartling.com/p/jobs-api/v3/accounts/{account_uid}/jobs?limit=50&offset=0&sortBy=createdDate&sortDirection=DESC&translationJobStatus=AWAITING_AUTHORIZATIONS&assignedToCurrentUser=false"

    try:
        r = requests.get(url, headers=headers, timeout=10)

        if r.status_code == 401:
            log.warning("⚠️ انتهت الجلسة (401).")
            return []

        if r.status_code != 200:
            log.warning(f"⚠️ API رجع {r.status_code}: {r.text[:200]}")
            return []

        data = r.json()
        jobs = data.get("response", {}).get("data", {}).get("items", [])
        return jobs

    except Exception as e:
        log.error(f"❌ خطأ في جلب الوظائف: {e}")
        return []


# ─────────────────────────────────────────────
# قبول الوظيفة مباشرة عبر API
# ─────────────────────────────────────────────
def accept_job(session: dict, job: dict) -> bool:
    """يقبل الوظيفة مباشرة بدون متصفح."""
    headers  = build_headers(session)
    project_uid      = job.get("projectId") or session.get("project_uid", "")
    job_uid          = job.get("translationJobUid", "")
    job_name         = job.get("jobName", "مجهولة")

    if not project_uid or not job_uid:
        log.error(f"❌ بيانات ناقصة: project={project_uid}, job={job_uid}")
        return False

    url = f"https://dashboard.smartling.com/p/content-assignments-api/v2/projects/{project_uid}/claiming/tasks/create"

    payload = {
        "localeId": LOCALE_ID,
        "workflowStepUid": WORKFLOW_STEP_UID,
        "translationJobUid": job_uid,
        "targetTaskWords": TARGET_TASK_WORDS
    }

    log.info(f"🎯 قبول الوظيفة: {job_name} ({job_uid})")

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)

        if r.status_code in [200, 201]:
            data = r.json()
            code = data.get("response", {}).get("code", "")
            if code == "SUCCESS":
                log.info(f"✅ تم قبول الوظيفة بنجاح: {job_name}")
                return True
            else:
                log.warning(f"⚠️ الاستجابة: {code} — {r.text[:300]}")
                return False

        elif r.status_code == 401:
            log.warning("⚠️ انتهت الجلسة أثناء القبول.")
            return False

        else:
            log.warning(f"⚠️ فشل القبول ({r.status_code}): {r.text[:300]}")
            return False

    except Exception as e:
        log.error(f"❌ خطأ أثناء قبول الوظيفة: {e}")
        return False


# ─────────────────────────────────────────────
# الحلقة الرئيسية
# ─────────────────────────────────────────────
async def monitor_loop():
    if not EMAIL or not PASSWORD:
        log.error("❌ يجب ضبط SMARTLING_EMAIL و SMARTLING_PASSWORD في ملف .env")
        return

    log.info("🚀 بدء تشغيل Smartling Bot v2 (API Mode)")
    log.info(f"⏱️  فترة الفحص: كل {CHECK_INTERVAL} ثانية")
    log.info(f"🌍 اللغة: {LOCALE_ID}")

    session       = None
    accepted_total = 0
    session_errors = 0

    while True:
        try:
            # جلب أو تجديد الجلسة
            if session is None or session_errors >= 3:
                session = await get_session()
                session_errors = 0
                if not session:
                    log.error("❌ فشل تسجيل الدخول. انتظار دقيقة...")
                    await asyncio.sleep(60)
                    continue

            loop_start = datetime.now()
            log.info(f"🔄 [{loop_start.strftime('%H:%M:%S')}] فحص الوظائف...")

            # جلب الوظائف
            jobs = fetch_available_jobs(session)

            if jobs is None or (isinstance(jobs, list) and len(jobs) == 0
                                and session_errors > 0):
                session_errors += 1
            else:
                session_errors = 0

            if jobs:
                log.info(f"🎉 وُجد {len(jobs)} وظيفة!")
                for job in jobs:
                    success = accept_job(session, job)
                    if success:
                        accepted_total += 1
                        log.info(f"📊 إجمالي الوظائف المقبولة: {accepted_total}")
                    elif not success:
                        # قد تكون الجلسة انتهت
                        session_errors += 1

            else:
                log.info(f"😴 لا وظائف. انتظار {CHECK_INTERVAL}ث...")

            # انتظار الفحص التالي
            elapsed = (datetime.now() - loop_start).total_seconds()
            wait    = max(1, CHECK_INTERVAL - elapsed)
            await asyncio.sleep(wait)

        except KeyboardInterrupt:
            log.info("🛑 تم الإيقاف.")
            break
        except Exception as e:
            log.error(f"❌ خطأ غير متوقع: {e}")
            await asyncio.sleep(30)

    log.info(f"📊 الإجمالي النهائي: {accepted_total} وظيفة مقبولة")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(monitor_loop())
