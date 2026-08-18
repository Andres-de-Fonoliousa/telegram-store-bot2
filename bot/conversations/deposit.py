# bot/conversations/deposit.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from core.database import SessionLocal
from core.models import User, DepositOrder
from config import settings
from bot.handlers.navigation import exit_to_main_menu, exit_to_profile, exit_to_categories
from bot.handlers.user import show_games_category, show_social_category, show_profile

logger = logging.getLogger(__name__)

# حالات المحادثة
ASK_AMOUNT, CHOOSE_METHOD, GET_TRANSACTION, CONFIRM_DEPOSIT = range(4)

# --- الأرقام والمعلومات الثابتة ---
SYRIATEL_CASH_NUMBER = "0935659516"
SHAM_CASH_ID = "82ffb6a196b7d7b76f11452316256108"
# ⚠️ ضع هنا file_id صورة شام كاش بعد الحصول عليه (مثل "AgAC...")
SHAM_CASH_IMAGE_ID = "ضع_هنا_معرف_الصورة_من_تيليجرام"

# --- دوال الخروج أثناء المحادثة (مستوردة من navigation) ---
# exit_to_main_menu, exit_to_profile, exit_to_categories

# --- معالجات مشتركة لأزرار الرد ---
# --- معالجات أزرار الرد (تذهب مباشرة للقسم المطلوب) ---
async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💰 *شحن الرصيد*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "أدخل المبلغ الذي تريد شحنه (بالليرة السورية):",
        parse_mode="Markdown"
    )
    context.user_data.clear()
    return ASK_AMOUNT

async def choose_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text)
        if amount <= 0:
            raise ValueError
        context.user_data["deposit_amount"] = amount
    except Exception:
        await update.message.reply_text("❌ يرجى إدخال مبلغ صحيح باستخدام الأرقام فقط.")
        return ASK_AMOUNT

    keyboard = [
        [InlineKeyboardButton("📱 سيريتيل كاش", callback_data="method_syriatel")],
        [InlineKeyboardButton("🏦 شام كاش", callback_data="method_sham")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="method_back")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_deposit")]
    ]
    await update.message.reply_text(
        f"💳 *اختر طريقة الدفع*\n"
        f"المبلغ: {amount} ل.س\n"
        f"━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CHOOSE_METHOD

menu_handlers = [
    MessageHandler(filters.Regex('^الألعاب 🎮🔥$'), show_games_category),
    MessageHandler(filters.Regex('^الرَّشق ⚡📱$'), show_social_category),
    MessageHandler(filters.Regex('^شحن الرصيد 💎$'), start_deposit),  # أثناء الشحن، الضغط عليه يلغي
    MessageHandler(filters.Regex('^حسابي 👤$'), show_profile),
    CommandHandler("start", exit_to_main_menu),
    CommandHandler("profile", exit_to_profile),
    CommandHandler("charge", exit_to_main_menu),
]

async def show_payment_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel_deposit":
        await query.edit_message_text("❌ تم إلغاء طلب الشحن.")
        return ConversationHandler.END
    if data == "method_back":
        await query.edit_message_text("🔙 أرجع وأدخل المبلغ الجديد:")
        return ASK_AMOUNT

    if data == "method_syriatel":
        context.user_data["deposit_method"] = "syriatel"
        text = (
            f"📱 *الدفع عبر سيريتيل كاش*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📞 الرجاء التحويل إلى الرقم:\n"
            f"<code>{SYRIATEL_CASH_NUMBER}</code>\n\n"
            f"🔄 ثم أرسل *رقم العملية* أو *لقطة شاشة* للإيصال:"
        )
        await query.edit_message_text(text, parse_mode="HTML")
    elif data == "method_sham":
        context.user_data["deposit_method"] = "sham"
        # إذا كانت الصورة متوفرة، نرسلها مع النص، وإلا نرسل النص فقط
        if SHAM_CASH_IMAGE_ID and SHAM_CASH_IMAGE_ID != "ضع_هنا_معرف_الصورة_من_تيليجرام":
            try:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=SHAM_CASH_IMAGE_ID,
                    caption=(
                        f"🏦 *الدفع عبر شام كاش*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🆔 معرف المحفظة:\n"
                        f"<code>{SHAM_CASH_ID}</code>\n\n"
                        f"🔄 أرسل *رقم العملية* (Transaction ID):"
                    ),
                    parse_mode="HTML"
                )
                await query.delete_message()
            except Exception as e:
                logger.warning(f"Could not send sham cash image: {e}")
                await query.edit_message_text(
                    f"🏦 *الدفع عبر شام كاش*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🆔 معرف المحفظة:\n"
                    f"<code>{SHAM_CASH_ID}</code>\n\n"
                    f"🔄 أرسل *رقم العملية* (Transaction ID):",
                    parse_mode="HTML"
                )
        else:
            await query.edit_message_text(
                f"🏦 *الدفع عبر شام كاش*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🆔 معرف المحفظة:\n"
                f"<code>{SHAM_CASH_ID}</code>\n\n"
                f"🔄 أرسل *رقم العملية* (Transaction ID):",
                parse_mode="HTML"
            )
    else:
        await query.edit_message_text("❌ خيار غير معروف.")
        return ConversationHandler.END

    return GET_TRANSACTION


async def receive_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تستقبل صورة أو نص (رقم العملية) وتخزنها."""
    if update.message.photo:
        # حفظ معرف الصورة
        file_id = update.message.photo[-1].file_id
        context.user_data["screenshot_file_id"] = file_id
        context.user_data["is_photo"] = True
        await update.message.reply_text("✅ تم استلام لقطة الشاشة.")
    else:
        # حفظ النص كرقم عملية
        transaction_id = update.message.text.strip()
        context.user_data["screenshot_file_id"] = transaction_id
        context.user_data["is_photo"] = False
        await update.message.reply_text(f"✅ تم استلام رقم العملية: {transaction_id}")

    # عرض ملخص التأكيد
    amount = context.user_data["deposit_amount"]
    method = context.user_data["deposit_method"]
    method_text = "📱 سيريتيل كاش" if method == "syriatel" else "🏦 شام كاش"
    trans = context.user_data["screenshot_file_id"]
    trans_display = f"<code>{trans}</code>" if not context.user_data["is_photo"] else "📸 لقطة شاشة"

    summary = (
        f"📝 *تأكيد الشحن*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 المبلغ: {amount} ل.س\n"
        f"💳 الوسيلة: {method_text}\n"
        f"🔢 العملية: {trans_display}"
    )
    await update.message.reply_text(
        summary,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأكيد الشحن", callback_data="confirm_deposit")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="confirm_back")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_deposit")],
        ])
    )
    return CONFIRM_DEPOSIT


async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # --- معالجة زر الرجوع ---
    if query.data == "confirm_back":
        amount = context.user_data.get("deposit_amount", 0)
        keyboard = [
            [InlineKeyboardButton("📱 سيريتيل كاش", callback_data="method_syriatel")],
            [InlineKeyboardButton("🏦 شام كاش", callback_data="method_sham")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="method_back")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_deposit")]
        ]
        await query.edit_message_text(
            f"💳 *اختر طريقة الدفع*\n"
            f"المبلغ: {amount} ل.س\n"
            f"━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return CHOOSE_METHOD

    query = update.callback_query
    await query.answer()
    if query.data == "cancel_deposit":
        await query.edit_message_text("❌ تم إلغاء طلب الشحن.")
        return ConversationHandler.END

    db = SessionLocal()
    try:
        telegram_user = query.from_user
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

        deposit = DepositOrder(
            user_id=user.id,
            amount=context.user_data["deposit_amount"],
            screenshot_path=context.user_data["screenshot_file_id"],
            status="pending"
        )
        db.add(deposit)
        db.commit()
        db.refresh(deposit)

        # إشعار المشرفين
        admin_ids = getattr(settings, "ADMIN_IDS", [])
        amount = context.user_data["deposit_amount"]
        method = context.user_data["deposit_method"]
        method_text = "سيريتيل كاش" if method == "syriatel" else "شام كاش"
        user_info = f"{user.first_name or ''} {user.last_name or ''} (@{user.username or ''})"

        caption = (
            f"طلب شحن جديد\n"
            f"المستخدم: {user_info}\n"
            f"المبلغ: {amount} ل.س\n"
            f"الوسيلة: {method_text}\n"
            f"رقم الطلب: {deposit.id}\n"
        )
        screenshot = context.user_data["screenshot_file_id"]
        is_photo = context.user_data.get("is_photo", False)

        for admin_id in admin_ids:
            try:
                if is_photo:
                    await context.bot.send_photo(
                        chat_id=admin_id,
                        photo=screenshot,
                        caption=caption
                    )
                else:
                    full_text = caption + f"رقم العملية: {screenshot}"
                    await context.bot.send_message(chat_id=admin_id, text=full_text)
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")

        await query.edit_message_text(
            "🎉 *تم إرسال طلب الشحن*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📤 طلبك قيد المراجعة من قبل الإدارة.\n"
            "🔔 سنقوم بإشعارك فور الموافقة."
        )
    except Exception as e:
        logger.error(f"Error in handle_confirmation: {e}")
        await query.edit_message_text("❌ حدث خطأ أثناء معالجة طلب الشحن. يرجى المحاولة لاحقاً.")
    finally:
        db.close()
    return ConversationHandler.END


async def cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء عملية الشحن — يعمل مع أزرار inline ومع الأمر /cancel."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ تم إلغاء عملية الشحن.")
    else:
        await update.message.reply_text("❌ تم إلغاء عملية الشحن.")
    return ConversationHandler.END


def deposit_conversation_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("charge", start_deposit)],
        states={
            ASK_AMOUNT: menu_handlers + [
                MessageHandler(filters.TEXT & ~filters.COMMAND, choose_method),
            ],
            CHOOSE_METHOD: menu_handlers + [   # <-- أضفنا menu_handlers هنا
                CallbackQueryHandler(show_payment_instructions, pattern="^method_"),
                CallbackQueryHandler(cancel_deposit, pattern="^cancel_deposit$"),
            ],
            GET_TRANSACTION: menu_handlers + [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_transaction),
                MessageHandler(filters.PHOTO, receive_transaction),
            ],
            CONFIRM_DEPOSIT: menu_handlers + [  # <-- أضفنا menu_handlers هنا
                CallbackQueryHandler(handle_confirmation, pattern="^(confirm_deposit|cancel_deposit|confirm_back)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_deposit),
            CommandHandler("start", exit_to_main_menu),
            CommandHandler("profile", exit_to_profile),
            CommandHandler("charge", exit_to_main_menu),
        ],
    )