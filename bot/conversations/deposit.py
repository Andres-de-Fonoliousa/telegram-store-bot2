# bot/conversations/deposit.py
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from core.database import SessionLocal
from core.models import User, DepositOrder
from config import settings

ASK_AMOUNT, ASK_SCREENSHOT, CONFIRM_DEPOSIT = range(3)

async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💸 كم المبلغ الذي تريد شحنه (بالليرة السورية)؟")
    context.user_data.clear()
    return ASK_AMOUNT

async def ask_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text)
        if amount <= 0:
            raise ValueError
        context.user_data["deposit_amount"] = amount
        await update.message.reply_text(
            "ارسل المبلغ الى أحد الأرقام الموضحة\n"
            "سيريتيل كاش: <code>86344754</code>\n"
            "شام كاش: <code>272b058a8dbe4cf608fed50fb1e23e8c</code>\n"
            "📸 يرجى إرسال لقطة شاشة لإثبات التحويل.",
            parse_mode="HTML"
        )
        return ASK_SCREENSHOT
    except Exception:
        await update.message.reply_text("❌ يرجى إدخال مبلغ صحيح.")
        return ASK_AMOUNT

async def confirm_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ يرجى إرسال صورة فقط.")
        return ASK_SCREENSHOT
    file_id = update.message.photo[-1].file_id
    context.user_data["screenshot_file_id"] = file_id
    amount = context.user_data["deposit_amount"]
    await update.message.reply_text(
        f"سيتم إرسال طلب شحن بقيمة {amount} ل.س للمراجعة.\nاضغط تأكيد لإرسال الطلب.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأكيد الشحن", callback_data="confirm_deposit")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_deposit")],
        ])
    )
    return CONFIRM_DEPOSIT

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import logging
    query = update.callback_query
    await query.answer()
    logging.info("[Deposit] Confirmation callback received: %s", query.data)
    if query.data == "cancel_deposit":
        await query.edit_message_text("❌ تم إلغاء طلب الشحن.")
        logging.info("[Deposit] Deposit cancelled by user.")
        return ConversationHandler.END
    db = SessionLocal()
    try:
        telegram_user = query.from_user
        logging.info(f"[Deposit] Telegram user: {telegram_user.id} ({telegram_user.username})")
        user = db.query(User).filter_by(telegram_id=telegram_user.id).first()
        if not user:
            user = User(
                telegram_id=telegram_user.id,
                username=telegram_user.username,
                first_name=telegram_user.first_name,
                last_name=telegram_user.last_name
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logging.info(f"[Deposit] Created new user: {user.id}")
        deposit = DepositOrder(
            user_id=user.id,
            amount=context.user_data["deposit_amount"],
            screenshot_path=context.user_data["screenshot_file_id"],
            status="pending_payment"
        )
        db.add(deposit)
        db.commit()
        db.refresh(deposit)
        logging.info(f"[Deposit] Created deposit order: {deposit.id}")
        # Notify admin
        admin_ids = getattr(settings, "ADMIN_IDS", [])
        admin_chat_id = admin_ids[0] if admin_ids else None
        admin_notify_success = False
        if admin_chat_id:
            bot_token = getattr(settings, "BOT_TOKEN", None)
            text = (
                f"طلب شحن جديد من المستخدم: {user.first_name or ''} {user.last_name or ''} (@{user.username or ''})\n"
                f"المبلغ: {deposit.amount} ل.س\nرقم الطلب: {deposit.id}\nانتظر موافقة الإدارة."
            )
            url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            payload = {"chat_id": admin_chat_id, "caption": text, "photo": context.user_data["screenshot_file_id"]}
            import requests
            try:
                resp = requests.post(url, data=payload, timeout=5)
                if resp.status_code == 200:
                    admin_notify_success = True
                    logging.info(f"[Deposit] Admin notified successfully.")
                else:
                    logging.error(f"[Deposit] Admin notify failed: {resp.status_code} {resp.text}")
            except Exception as e:
                logging.error(f"[Deposit] Failed to notify admin: {e}")
        await query.edit_message_text("✅ تم إرسال طلب الشحن بنجاح! سيتم مراجعة الطلب من قبل الإدارة.")
        logging.info("[Deposit] Confirmation message sent to user.")
    except Exception as e:
        logging.error(f"[Deposit] Error in handle_confirmation: {e}")
        await query.edit_message_text("❌ حدث خطأ أثناء معالجة طلب الشحن. يرجى المحاولة لاحقاً.")
    finally:
        db.close()
    return ConversationHandler.END

async def cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم إلغاء عملية الشحن.")
    return ConversationHandler.END

def deposit_conversation_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("charge", start_deposit)],
        states={
            ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_screenshot)],
            ASK_SCREENSHOT: [MessageHandler(filters.PHOTO, confirm_deposit)],
            CONFIRM_DEPOSIT: [CallbackQueryHandler(handle_confirmation, pattern="^(confirm_deposit|cancel_deposit)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_deposit)],
    )
