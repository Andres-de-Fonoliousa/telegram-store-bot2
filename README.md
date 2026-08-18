# متجر تيليغرام — Bot + لوحة تحكم

بوت متجر كامل للتيليغرام مع لوحة إدارة ويب (Flask): تصفح المنتجات، طلبات شراء برصيد داخلي، شحن الرصيد عبر تحويل وإيصال، ربط بمزوّد شحن خارجي (MHD / shams4store API)، وإحصائيات الأرباح.

## الميزات

- تصفح متعدد المستويات: `الألعاب 🎮🔥` — `الرَّشق ⚡📱` — `شحن الرصيد 💎`
- نموذج طلب ديناميكي: كل منتج له حقول مخصصة (يوسر/برسورد/قيمة/كمية...)
- رصيد داخلي: شحن عبر تحويل وإرفاق صورة الإيصال + موافقة الأدمن
- سلاسل الإشعارات (تنبيهات للمشترين بخصوص عمليات ملغاة أو مؤكدة)
- شراء/استرداد آلي عبر مزوّد شحن مدمج (MHD API) مع مزامنة الأسعار وفحص الرصيد
- لوحة إدارة ويب: الطلبات، الشحنات، الأسعار، سعر الصرف، التحكم بالبوت، إحصائيات الأرباح
- إدارة من قطعتين: البوت (بولنق الإشعارات) + لوحة Flask (فلترة وإحصائيات)

## المتطلبات

- Python 3.10+
- pip packages (من `requirements.txt`)

## التثبيت

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  — أو source .venv/bin/activate على Linux
pip install -r requirements.txt
cp .env.example .env            # ثم املأ القيم (أنظر الأسفل)
```

## الإعداد (`config.py` / `.env`)

| مفتاح | الوصف |
|---|---|
| `BOT_TOKEN` | توكن البوت من @BotFather |
| `ADMIN_USER_IDS` | أرقام (Telegram IDs) الأدمن، مفصولة بفواصل |
| `DATABASE_URL` | ربط قاعدة البيانات (افتراضي: `sqlite:///./store.db`) |
| `FLASK_SECRET_KEY` | مفتاح توقيع لوحة الإدارة — قيمة عشوائية طويلة! |
| `ADMIN_HOST` / `ADMIN_PORT` | عناوين لوحة الإدارة (افتراضي `127.0.0.1:5000`) |
| `MHD_API_KEY` / `MHD_API_BASE_URL` | اعتماديات مزوّد الشحن الخارجي |
| `MHD_API_ENABLED` | تشغيل/إيقاف الشراء الآلي (يجب أن يكون الأدمن مسجّلاً) |
| `SYNC_ENABLED` / `SYNC_INTERVAL_MINUTES` | مزامنة الأسعار تلقائياً |
| `MHD_LOW_BALANCE_THRESHOLD` / `MHD_BALANCE_CHECK_ENABLED` | تنبيه الأدمن عند انخفاض رصيد المزوّد |

> ❗ مهم: لوحة الإدارة تعتمد بشكل افتراضي على `127.0.0.1` (الطريقي فقط على نفس الجهاز).
> في الإنتاج ضعه خلف nginx مع TLS — لا تفتحه على الإنترنيت مباشرة.

## التشغيل

```bash
python main.py                 # يشغّل البوت + لوحة الإدارة معاً
```

افصل اللوحة إن أردت:

```bash
python admin/app.py
```

ثم افتح `http://127.0.0.1:5000` وسجّل الدخول برمز الوصول.

### النظام (Linux / systemd)

```ini
[Unit]
Description=Store Bot
After=network.target

[Service]
WorkingDirectory=/opt/store-bot
ExecStart=/opt/store-bot/.venv/bin/python main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## الكود

- `main.py` — نقطة الدخول (يشغّل البوت ولوحة الإدارة)
- `bot/` — معالجات التليغراف والإرسالات
- `core/` — النّماذج وقاعدة البيانات
- `services/` — مزوّد API ومزامنة الأسعار
- `admin/` — لوحة الويب Flask + القوالب

## الأمان

المُثبّتة: جلسات مُوقَّعة بـ `FLASK_SECRET_KEY`، حماية CSRF على كل نماذج اللوحة، رمز وصول مُخزَّن كهاش، حد 5 محاولات تسجيل، وضمان عدم ازدواج الإيداع/الاسترداد عبر `balance_credited` و `refunded`.

## الترخيص

MIT — انظر [LICENSE](LICENSE).