# 🤖 Smartling Auto-Accept Bot

بوت يعمل 24/7 يراقب صفحة الوظائف على Smartling ويقبلها تلقائياً بمجرد ظهورها.

---

## ⚡ الإعداد السريع

### 1. تثبيت Python
تأكد أن Python 3.9+ مثبت:
```
python --version
```

### 2. تثبيت المكتبات
```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. إعداد بياناتك
افتح ملف `.env` وضع بياناتك:
```
SMARTLING_EMAIL=your_email@example.com
SMARTLING_PASSWORD=your_password_here
CHECK_INTERVAL=15
```

### 4. تشغيل البوت
```bash
python smartling_bot.py
```

---

## 🔧 الخيارات

| المتغير | الوصف | الافتراضي |
|---------|-------|-----------|
| `SMARTLING_EMAIL` | إيميل حسابك | مطلوب |
| `SMARTLING_PASSWORD` | كلمة المرور | مطلوبة |
| `CHECK_INTERVAL` | ثواني بين كل فحص | 15 |

---

## 📁 الملفات المنشأة تلقائياً

| الملف | الغرض |
|-------|-------|
| `smartling_session.json` | الجلسة المحفوظة (لتجنب تسجيل الدخول في كل مرة) |
| `smartling_bot.log` | سجل كامل بكل العمليات |
| `error_*.png` | لقطات شاشة عند حدوث أخطاء |

---

## 🖥️ التشغيل المستمر (24/7)

### على Windows (Task Scheduler):
1. افتح Task Scheduler
2. Create Basic Task
3. Action: `python C:\path\to\smartling_bot.py`
4. Trigger: At Startup

### على Linux/Mac (systemd أو screen):
```bash
# خيار 1: screen
screen -S smartling
python smartling_bot.py
# Ctrl+A ثم D للخروج مع استمرار التشغيل

# خيار 2: nohup
nohup python smartling_bot.py &
```

---

## ⚙️ كيف يعمل

```
1. تسجيل الدخول مرة واحدة → حفظ الجلسة
        ↓
2. كل 15 ثانية: فتح صفحة AVAILABLE_TO_ACCEPT
        ↓
3. رصد الوظائف المتاحة
        ↓
4. [إن وُجدت] اضغط Accept → حدد الكل → أكد القبول
        ↓
5. تسجيل العملية في log
        ↓
6. انتظار وتكرار من الخطوة 2
```

---

## 🐛 استكشاف الأخطاء

- **لا يتم تسجيل الدخول**: تحقق من `.env` والبيانات
- **لا تُكتشف الوظائف**: قلل `CHECK_INTERVAL` إلى 5 ثواني
- **يظهر خطأ popup**: انظر `popup_debug_*.png` لفهم الواجهة
- **انتهت الجلسة**: احذف `smartling_session.json` وأعد التشغيل
