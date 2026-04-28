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

async def exit_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔙 تم العودة للقائمة الرئيسية.")
    from bot.handlers.user import show_main_menu
    await show_main_menu(update, context)
    return ConversationHandler.END

async def exit_to_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔙 جاري عرض الملف الشخصي...")
    from bot.handlers.user import show_profile
    await show_profile(update, context)
    return ConversationHandler.END

async def exit_to_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("💰 جاري الانتقال إلى الشحن...")
    # استدعاء أمر /charge مباشرة (كما يفعل زر شحن الرصيد)
    return await start_deposit(update, context)

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
        # Notify ALL admins
        admin_ids = getattr(settings, "ADMIN_IDS", [])
        photo_file_id = context.user_data["screenshot_file_id"]
        caption = (
            f"طلب شحن جديد من المستخدم: {user.first_name or ''} {user.last_name or ''} (@{user.username or ''})\n"
            f"المبلغ: {deposit.amount} ل.س\nرقم الطلب: {deposit.id}\nانتظر موافقة الإدارة."
        )
        for admin_id in admin_ids:
            try:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo_file_id,
                    caption=caption
                )
                logging.info(f"[Deposit] Admin {admin_id} notified successfully.")
            except Exception as e:
                logging.error(f"[Deposit] Failed to notify admin {admin_id}: {e}")

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
from order import exit_to_categories
def deposit_conversation_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("charge", start_deposit)],
        states={
            ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_screenshot)],
            ASK_SCREENSHOT: [MessageHandler(filters.PHOTO, confirm_deposit)],
            CONFIRM_DEPOSIT: [CallbackQueryHandler(handle_confirmation, pattern="^(confirm_deposit|cancel_deposit)$")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_deposit),
            CommandHandler("cancel", cancel_deposit),
            CommandHandler("start", exit_to_main_menu),
            CommandHandler("profile", exit_to_profile),
            MessageHandler(filters.Regex('^🛒 الأقسام$'), exit_to_categories),
            MessageHandler(filters.Regex('^💰 شحن الرصيد$'), cancel_deposit),  # زر الشحن أثناء الشحن يلغي
            MessageHandler(filters.Regex('^👤 حسابي$'), exit_to_profile),
                   ],
    )
