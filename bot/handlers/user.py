# bot/handlers/user.py
from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from bot.keyboards.inline import (
    main_menu_keyboard,
    products_keyboard,
    back_to_main_keyboard,
)
from bot.keyboards.reply import main_reply_keyboard
from core.database import SessionLocal
from core.models import Category, Product


# ========== دوال العرض الأساسية (تُستدعى من main.py ومن داخل الملف) ==========

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    عرض القائمة الرئيسية (الأقسام) مع مسح أي حالة مؤقتة.
    تُستخدم عند الضغط على 'القائمة الرئيسية' من أي مكان.
    """
    # مسح بيانات المستخدم لإنهاء أي محادثة جارية
    context.user_data.clear()

    text = "الرجاء اختيار القسم:"
    keyboard = main_menu_keyboard()

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    عرض قائمة الأقسام (نفس وظيفة show_main_menu، للتوافق مع الاسم المستخدم في main.py).
    """
    await show_main_menu(update, context)


# ========== معالج الردود النصية (لوحة المفاتيح الرئيسية) ==========

async def reply_keyboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأزرار النصية أسفل الشاشة."""
    text = update.message.text

    if text == "🛒 الأقسام":
        await show_main_menu(update, context)
        return

    elif text == "💰 شحن الرصيد":
        # بدء محادثة الإيداع عبر إرسال أمر /charge
        await update.message._bot.send_message(
            chat_id=update.effective_chat.id,
            text="/charge  الرجاء الضغط على أمر"
        )
        return

    # باقي النصوص تُترك لمحادثات أخرى (مثل order/deposit)


reply_keyboard_message_handler = MessageHandler(
    filters.TEXT & ~filters.COMMAND,
    reply_keyboard_handler
)


# ========== أمر /start ==========

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = (
        f"أهلاً وسهلاً {user.first_name}! 👋\n\n"
        f"متجر الشحن الإلكتروني - الرجاء اختيار القسم:"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=main_reply_keyboard()
    )


start_handler = CommandHandler("start", start_command)


# ========== معالج الأزرار الداخلية (Inline) ==========

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج عام لجميع الـ Callback Query ما لم تلتقطها ConversationHandlers."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # --- القسم المحدد ---
    if data.startswith("category_"):
        cat_id = int(data.replace("category_", ""))
        db = SessionLocal()
        try:
            category = db.query(Category).filter_by(id=cat_id).first()
            if category:
                products = db.query(Product).filter_by(
                    category_id=category.id,
                    is_active=True
                ).all()
                if products:
                    await query.edit_message_text(
                        f"📂 {category.name_ar}\nاختر المنتج:",
                        reply_markup=products_keyboard(products)
                    )
                else:
                    await query.edit_message_text(
                        f"⚠️ لا توجد منتجات حالياً في قسم {category.name_ar}",
                        reply_markup=back_to_main_keyboard()
                    )
            else:
                await query.edit_message_text(
                    "❌ القسم غير موجود",
                    reply_markup=back_to_main_keyboard()
                )
        finally:
            db.close()

    # --- العودة للقائمة الرئيسية (من داخل inline keyboard) ---
    elif data == "back_to_main":
        await show_main_menu(update, context)

    # --- أزرار أخرى (تترك للمحادثات) ---
    # لا تفعل شيئًا هنا؛ ConversationHandlers ستلتقطها
    # (مثل product_*, confirm_deposit, cancel_deposit...)


# معالج Callback عام (يُستخدم في main.py)
callback_handler = CallbackQueryHandler(button_callback)