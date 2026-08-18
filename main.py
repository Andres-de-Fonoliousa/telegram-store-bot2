# main.py
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    TypeHandler,
    filters,
)
from config import settings
from core.database import run_migrations

# استيراد دوال الخلفية
from bot.conversations.order import recover_polling_jobs
from services.price_sync import sync_prices_from_mhd

run_migrations()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# حالة البوت العامة
bot_active = True

# استثناء إيقاف المعالجة
try:
    from telegram.ext import ApplicationHandlerStop
except ImportError:
    class ApplicationHandlerStop(Exception):
        pass

# --- فحص الحالة العام لكل تحديث ---
async def global_bot_check(update: Update, _: ContextTypes.DEFAULT_TYPE):
    # السماح دائماً للمشرفين
    if update.effective_user and update.effective_user.id in settings.ADMIN_IDS:
        return

    global bot_active
    if not bot_active:
        if update.message:
            await update.message.reply_text("⚠️ البوت متوقف حالياً للصيانة. يرجى المحاولة لاحقاً.")
        elif update.callback_query:
            await update.callback_query.answer("⚠️ البوت متوقف حالياً للصيانة.", show_alert=True)
        raise ApplicationHandlerStop()

# --- معالج أزرار القائمة الرئيسية (لإنهاء المحادثات العالقة) ---
# ملاحظة: تم حذف global_menu_handler لأن callback data الفعلية هي
# "back_to_main" وليست "categories"/"main_menu"؛ إنهاء المحادثات يتم الآن
# داخل كل ConversationHandler عبر معالج pattern="^back_to_main$".

# --- أوامر المشرف للتحكم بالبوت ---
async def start_bot_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    global bot_active
    bot_active = True
    await update.message.reply_text("✅ تم تشغيل البوت وهو الآن نشط.")

async def stop_bot_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    global bot_active
    bot_active = False
    await update.message.reply_text("⏸️ تم إيقاف البوت مؤقتاً. لن يتم قبول أي طلبات جديدة.")

async def bot_status_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    state = "نشط ✅" if bot_active else "متوقف ⏸️"
    await update.message.reply_text(f"حالة البوت الحالية: {state}")

from core.database import SessionLocal
from core.models import User

async def set_discount_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعيين نسبة خصم لمستخدم: /setdiscount <user_id> <percent>"""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("الاستخدام: /setdiscount <معرف_المستخدم> <النسبة_المئوية>")
        return
    try:
        target_id = int(context.args[0])
        percent = int(context.args[1])
        if percent < 0 or percent > 100:
            raise ValueError
    except ValueError:
        await update.message.reply_text("يرجى إدخال أرقام صحيحة (0-100).")
        return

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(telegram_id=target_id).first()
        if not user:
            await update.message.reply_text("❌ المستخدم غير موجود.")
            return
        user.discount_percent = percent
        db.commit()
        await update.message.reply_text(f"✅ تم تعيين خصم {percent}% للمستخدم {target_id}.")
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {e}")
    finally:
        db.close()

async def remove_discount_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إزالة خصم مستخدم: /removediscount <user_id>"""
    if not context.args:
        await update.message.reply_text("الاستخدام: /removediscount <معرف_المستخدم>")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("يرجى إدخال معرف مستخدم صحيح.")
        return

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(telegram_id=target_id).first()
        if not user:
            await update.message.reply_text("❌ المستخدم غير موجود.")
            return
        user.discount_percent = 0
        db.commit()
        await update.message.reply_text(f"✅ تم إزالة الخصم عن المستخدم {target_id}.")
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {e}")
    finally:
        db.close()

# --- الدالة الرئيسية ---
def main() -> None:
    application = ApplicationBuilder().token(settings.BOT_TOKEN).build()

    # 1. فلتر حالة البوت (أعلى أولوية)
    application.add_handler(TypeHandler(Update, global_bot_check), group=-1)

    # 2. استيراد جميع الهاندلرز
    from bot.handlers.user import (
        start_handler,
        callback_handler,
        reply_keyboard_message_handler
    )
    from bot.conversations.order import order_conversation_handler
    from bot.conversations.deposit import deposit_conversation_handler
    from bot.conversations.admin import admin_conversation_handler
    from bot.handlers.user import show_profile
    # 4. إضافة الهاندلرز بالترتيب
    application.add_handler(start_handler)
    application.add_handler(order_conversation_handler())
    application.add_handler(deposit_conversation_handler())
    application.add_handler(admin_conversation_handler())
    application.add_handler(callback_handler)
    application.add_handler(reply_keyboard_message_handler)
    application.add_handler(CommandHandler("profile", show_profile))

    # 5. أوامر المشرف للتحكم بالبوت
    admin_filter = filters.User(user_id=settings.ADMIN_IDS)
    application.add_handler(CommandHandler("start_bot", start_bot_cmd, filters=admin_filter))
    application.add_handler(CommandHandler("stop_bot", stop_bot_cmd, filters=admin_filter))
    application.add_handler(CommandHandler("bot_status", bot_status_cmd, filters=admin_filter))
    application.add_handler(CommandHandler("setdiscount", set_discount_cmd, filters=admin_filter))
    application.add_handler(CommandHandler("removediscount", remove_discount_cmd, filters=admin_filter))
    # 6. مهام الخلفية (مزامنة الأسعار واسترداد الاستطلاع)
    job_queue = application.job_queue
    if job_queue:
        if settings.MHD_API_ENABLED and getattr(settings, 'SYNC_ENABLED', False):
            interval = getattr(settings, 'SYNC_INTERVAL_MINUTES', 60) * 60
            job_queue.run_repeating(sync_prices_from_mhd, interval=interval, first=10)
            logger.info(f"Price sync scheduled every {interval//60} minutes")

        # استرداد مهام استطلاع الطلبات المعلقة بعد إعادة التشغيل
        job_queue.run_once(
            recover_polling_jobs,
            when=2,
            name="recover_polling_jobs"
        )

    print("Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()