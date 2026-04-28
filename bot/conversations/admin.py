import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from core.database import SessionLocal
from core.models import User, Order, DepositOrder, Product
from core.notifications import send_notification
from config import settings

logger = logging.getLogger(__name__)

# States (added BOT_CONTROL)
(
    MAIN_MENU,
    VIEW_ORDERS,
    VIEW_DEPOSITS,
    ORDER_DETAIL,
    DEPOSIT_DETAIL,
    ENTER_CODE,
    BOT_CONTROL,          # new state for bot control submenu
) = range(7)

ADMIN_IDS = settings.ADMIN_IDS

# ---------- Helpers ----------
def is_admin(telegram_id: int) -> bool:
    return str(telegram_id) in [str(aid) for aid in ADMIN_IDS]

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_admin(user.id):
            if update.callback_query:
                await update.callback_query.answer("⛔ غير مصرح لك.", show_alert=True)
            elif update.message:
                await update.message.reply_text("⛔ هذا الأمر للمشرفين فقط.")
            return ConversationHandler.END
        return await func(update, context)
    return wrapper

def build_admin_menu_keyboard():
    buttons = [
        [InlineKeyboardButton("📦 الطلبات المعلقة", callback_data="admin_orders_pending")],
        [InlineKeyboardButton("📋 جميع الطلبات", callback_data="admin_orders_all")],
        [InlineKeyboardButton("💰 طلبات الشحن المعلقة", callback_data="admin_deposits_pending")],
        [InlineKeyboardButton("💵 جميع طلبات الشحن", callback_data="admin_deposits_all")],
        [InlineKeyboardButton("⚙️ التحكم بالبوت", callback_data="admin_bot_control")],
        [InlineKeyboardButton("🚪 خروج", callback_data="admin_exit")],
    ]
    return InlineKeyboardMarkup(buttons)

def back_to_menu_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="admin_back_to_menu")]])

def format_order_details(order, user, product_name, field_answers):
    fields = json.loads(field_answers) if field_answers else {}
    lines = [
        f"🆔 رقم الطلب: #{order.id}",
        f"👤 المستخدم: {user.first_name or ''} {user.last_name or ''}",
        f"📱 يوزر: @{user.username or 'لا يوجد'}",
        f"🛒 المنتج: {product_name}",
        f"💰 السعر: {order.total_price_syp} ل.س",
        f"📊 الحالة: {order.status}",
        f"📅 التاريخ: {order.created_at.strftime('%Y-%m-%d %H:%M') if order.created_at else '—'}",
    ]
    if fields:
        lines.append("\n📋 التفاصيل:")
        for k, v in fields.items():
            lines.append(f"  • {k}: {v}")
    if order.code_delivered:
        lines.append(f"\n🔑 الكود: {order.code_delivered}")
    return "\n".join(lines)

def format_deposit_details(deposit, user):
    lines = [
        f"🆔 رقم الطلب: #{deposit.id}",
        f"👤 المستخدم: {user.first_name or ''} {user.last_name or ''}",
        f"📱 يوزر: @{user.username or 'لا يوجد'}",
        f"💰 المبلغ: {deposit.amount} ل.س",
        f"📊 الحالة: {deposit.status}",
        f"📅 التاريخ: {deposit.created_at.strftime('%Y-%m-%d %H:%M') if deposit.created_at else '—'}",
    ]
    return "\n".join(lines)

# ---------- Entry ----------
@admin_only
async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry command /admin – clears any previous admin data."""
    for key in list(context.user_data.keys()):
        if key.startswith("admin_"):
            del context.user_data[key]
    await update.message.reply_text(
        "🔐 لوحة تحكم المشرفين\nاختر الإجراء المطلوب:",
        reply_markup=build_admin_menu_keyboard()
    )
    return MAIN_MENU

# ---------- Main Menu Callback ----------
@admin_only
async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_exit":
        await query.edit_message_text("👋 تم الخروج من لوحة التحكم.")
        return ConversationHandler.END

    if data == "admin_back_to_menu":
        await query.edit_message_text("🔐 لوحة التحكم:", reply_markup=build_admin_menu_keyboard())
        return MAIN_MENU

    if data == "admin_bot_control":
        return await bot_control_menu(update, context)

    db = SessionLocal()
    try:
        if data == "admin_orders_pending":
            orders = db.query(Order).filter(Order.status.in_(["pending", "pending_payment"])).order_by(Order.id.desc()).limit(10).all()
            return await show_orders_list(query, orders, "الطلبات المعلقة", db)

        elif data == "admin_orders_all":
            orders = db.query(Order).order_by(Order.id.desc()).limit(10).all()
            return await show_orders_list(query, orders, "جميع الطلبات", db)

        elif data == "admin_deposits_pending":
            deposits = db.query(DepositOrder).filter(DepositOrder.status == "pending_payment").order_by(DepositOrder.id.desc()).limit(10).all()
            return await show_deposits_list(query, deposits, "طلبات الشحن المعلقة", db)

        elif data == "admin_deposits_all":
            deposits = db.query(DepositOrder).order_by(DepositOrder.id.desc()).limit(10).all()
            return await show_deposits_list(query, deposits, "جميع طلبات الشحن", db)

    except Exception as e:
        logger.error(f"Admin menu error: {e}", exc_info=True)
        await query.edit_message_text("❌ حدث خطأ، حاول مرة أخرى.", reply_markup=back_to_menu_button())
        return MAIN_MENU
    finally:
        db.close()

# ---------- Bot Control Submenu ----------
@admin_only
async def bot_control_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Get current bot status from global variable defined in main.py
    import main
    state_text = "نشط ✅" if main.bot_active else "متوقف ⏸️"

    buttons = [
        [InlineKeyboardButton("▶️ تشغيل البوت", callback_data="admin_start_bot")],
        [InlineKeyboardButton("⏸️ إيقاف البوت", callback_data="admin_stop_bot")],
        [InlineKeyboardButton("📊 حالة البوت", callback_data="admin_bot_status")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="admin_back_to_menu")],
    ]
    await query.edit_message_text(
        f"⚙️ *التحكم بالبوت*\n\nالحالة الحالية: {state_text}\nاختر العملية:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )
    return BOT_CONTROL

@admin_only
async def bot_control_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    import main

    if data == "admin_start_bot":
        main.bot_active = True
        await query.edit_message_text("✅ تم تشغيل البوت بنجاح.", reply_markup=back_to_menu_button())
        return MAIN_MENU

    elif data == "admin_stop_bot":
        main.bot_active = False
        await query.edit_message_text("⏸️ تم إيقاف البوت مؤقتاً.", reply_markup=back_to_menu_button())
        return MAIN_MENU

    elif data == "admin_bot_status":
        state = "نشط ✅" if main.bot_active else "متوقف ⏸️"
        await query.answer(f"حالة البوت: {state}", show_alert=True)
        # Stay in bot control submenu
        return await bot_control_menu(update, context)

    elif data == "admin_back_to_menu":
        await query.edit_message_text("🔐 لوحة التحكم:", reply_markup=build_admin_menu_keyboard())
        return MAIN_MENU

    return MAIN_MENU

# ---------- List Views ----------
async def show_orders_list(query, orders, title, db):
    if not orders:
        await query.edit_message_text(f"📭 لا توجد {title} حالياً.", reply_markup=back_to_menu_button())
        return MAIN_MENU

    buttons = []
    for order in orders:
        user = db.query(User).filter_by(id=order.user_id).first()
        product = db.query(Product).filter_by(id=order.product_id).first()
        product_name = product.name_ar if product else f"منتج #{order.product_id}"
        user_display = f"{user.first_name or ''} (@{user.username or '—'})" if user else "—"
        label = f"#{order.id} - {product_name[:20]} - {order.total_price_syp} ل.س"
        buttons.append([InlineKeyboardButton(label, callback_data=f"admin_order_{order.id}")])

    buttons.append([InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="admin_back_to_menu")])
    await query.edit_message_text(
        f"📋 *{title}* (أحدث 10 طلبات)\nاختر طلباً لعرض التفاصيل:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )
    return VIEW_ORDERS

async def show_deposits_list(query, deposits, title, db):
    if not deposits:
        await query.edit_message_text(f"📭 لا توجد {title} حالياً.", reply_markup=back_to_menu_button())
        return MAIN_MENU

    buttons = []
    for dep in deposits:
        user = db.query(User).filter_by(id=dep.user_id).first()
        user_display = f"{user.first_name or ''} (@{user.username or '—'})" if user else "—"
        label = f"#{dep.id} - {user_display[:25]} - {dep.amount} ل.س"
        buttons.append([InlineKeyboardButton(label, callback_data=f"admin_deposit_{dep.id}")])

    buttons.append([InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="admin_back_to_menu")])
    await query.edit_message_text(
        f"💰 *{title}* (أحدث 10 طلبات)\nاختر طلباً لعرض التفاصيل:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )
    return VIEW_DEPOSITS

# ---------- Order Detail ----------
@admin_only
async def view_order_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_back_to_menu":
        await query.edit_message_text("🔐 لوحة التحكم:", reply_markup=build_admin_menu_keyboard())
        return MAIN_MENU

    order_id = int(data.split("_")[-1])
    db = SessionLocal()
    try:
        order = db.query(Order).filter_by(id=order_id).first()
        if not order:
            await query.edit_message_text("❌ الطلب غير موجود.", reply_markup=back_to_menu_button())
            return MAIN_MENU

        user = db.query(User).filter_by(id=order.user_id).first()
        product = db.query(Product).filter_by(id=order.product_id).first()
        product_name = product.name_ar if product else f"منتج #{order.product_id}"

        text = format_order_details(order, user, product_name, order.field_answers)
        context.user_data["admin_current_order_id"] = order_id

        buttons = []
        if order.status in ["pending", "pending_payment"]:
            buttons.append([InlineKeyboardButton("✅ تعليم كمكتمل", callback_data="admin_order_complete")])
            buttons.append([InlineKeyboardButton("📝 إضافة كود", callback_data="admin_order_add_code")])
        if order.status != "cancelled":
            buttons.append([InlineKeyboardButton("❌ إلغاء الطلب", callback_data="admin_order_cancel")])
        buttons.append([InlineKeyboardButton("🔙 رجوع للطلبات", callback_data="admin_back_to_orders")])
        buttons.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="admin_back_to_menu")])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return ORDER_DETAIL
    finally:
        db.close()

# ---------- Deposit Detail ----------
@admin_only
async def view_deposit_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_back_to_menu":
        await query.edit_message_text("🔐 لوحة التحكم:", reply_markup=build_admin_menu_keyboard())
        return MAIN_MENU

    deposit_id = int(data.split("_")[-1])
    db = SessionLocal()
    try:
        deposit = db.query(DepositOrder).filter_by(id=deposit_id).first()
        if not deposit:
            await query.edit_message_text("❌ طلب الشحن غير موجود.", reply_markup=back_to_menu_button())
            return MAIN_MENU

        user = db.query(User).filter_by(id=deposit.user_id).first()
        text = format_deposit_details(deposit, user)
        context.user_data["admin_current_deposit_id"] = deposit_id

        # Send screenshot if exists
        if deposit.screenshot_path:
            try:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=deposit.screenshot_path,
                    caption="🖼️ صورة الإيصال"
                )
            except Exception as e:
                logger.warning(f"Could not send deposit screenshot: {e}")

        buttons = []
        if deposit.status == "pending_payment":
            buttons.append([InlineKeyboardButton("✅ قبول الشحن", callback_data="admin_deposit_approve")])
            buttons.append([InlineKeyboardButton("❌ رفض الشحن", callback_data="admin_deposit_reject")])
        buttons.append([InlineKeyboardButton("🔙 رجوع للشحنات", callback_data="admin_back_to_deposits")])
        buttons.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="admin_back_to_menu")])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return DEPOSIT_DETAIL
    finally:
        db.close()

# ---------- Order Actions ----------
@admin_only
async def order_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    order_id = context.user_data.get("admin_current_order_id")

    if action == "admin_back_to_orders":
        # Return to orders list (using same logic as menu callback)
        return await admin_menu_callback(update, context)

    if action == "admin_back_to_menu":
        await query.edit_message_text("🔐 لوحة التحكم:", reply_markup=build_admin_menu_keyboard())
        return MAIN_MENU

    db = SessionLocal()
    try:
        order = db.query(Order).filter_by(id=order_id).first()
        if not order:
            await query.edit_message_text("❌ الطلب غير موجود.")
            return MAIN_MENU

        user = db.query(User).filter_by(id=order.user_id).first()

        if action == "admin_order_complete":
            order.status = "completed"
            db.commit()
            await send_notification(context.bot, user.telegram_id, f"✅ تم إتمام طلبك #{order.id} بنجاح.")
            await query.edit_message_text(f"✅ تم تحديث الطلب #{order.id} إلى 'مكتمل'.", reply_markup=back_to_menu_button())
            return MAIN_MENU

        elif action == "admin_order_cancel":
            order.status = "cancelled"
            db.commit()
            await send_notification(context.bot, user.telegram_id, f"❌ تم إلغاء طلبك #{order.id}.")
            await query.edit_message_text(f"❌ تم إلغاء الطلب #{order.id}.", reply_markup=back_to_menu_button())
            return MAIN_MENU

        elif action == "admin_order_add_code":
            await query.edit_message_text("📝 أرسل الكود الذي سيتم تسليمه للعميل:")
            return ENTER_CODE

    except Exception as e:
        logger.error(f"Order action error: {e}", exc_info=True)
        await query.edit_message_text("❌ حدث خطأ.", reply_markup=back_to_menu_button())
        return MAIN_MENU
    finally:
        db.close()

# ---------- Deposit Actions ----------
@admin_only
async def deposit_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    deposit_id = context.user_data.get("admin_current_deposit_id")

    if action == "admin_back_to_deposits":
        return await admin_menu_callback(update, context)

    if action == "admin_back_to_menu":
        await query.edit_message_text("🔐 لوحة التحكم:", reply_markup=build_admin_menu_keyboard())
        return MAIN_MENU

    db = SessionLocal()
    try:
        deposit = db.query(DepositOrder).filter_by(id=deposit_id).first()
        if not deposit:
            await query.edit_message_text("❌ الطلب غير موجود.")
            return MAIN_MENU

        user = db.query(User).filter_by(id=deposit.user_id).first()

        if action == "admin_deposit_approve":
            if deposit.status == "pending_payment":
                deposit.status = "completed"
                user.balance += deposit.amount
                deposit.admin_id = query.from_user.id
                db.commit()
                await send_notification(
                    context.bot, user.telegram_id,
                    f"✅ تمت الموافقة على شحن {deposit.amount} ل.س.\nرصيدك الحالي: {user.balance} ل.س"
                )
                await query.edit_message_text(f"✅ تم قبول الشحن وإضافة المبلغ.", reply_markup=back_to_menu_button())
            else:
                await query.edit_message_text("⚠️ هذا الطلب ليس قيد الانتظار.", reply_markup=back_to_menu_button())
            return MAIN_MENU

        elif action == "admin_deposit_reject":
            deposit.status = "rejected"
            deposit.admin_id = query.from_user.id
            db.commit()
            await send_notification(context.bot, user.telegram_id, f"❌ تم رفض طلب الشحن #{deposit.id}.")
            await query.edit_message_text(f"❌ تم رفض الشحن.", reply_markup=back_to_menu_button())
            return MAIN_MENU

    except Exception as e:
        logger.error(f"Deposit action error: {e}", exc_info=True)
        await query.edit_message_text("❌ حدث خطأ.", reply_markup=back_to_menu_button())
        return MAIN_MENU
    finally:
        db.close()

# ---------- Enter Code ----------
@admin_only
async def receive_order_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    order_id = context.user_data.get("admin_current_order_id")
    db = SessionLocal()
    try:
        order = db.query(Order).filter_by(id=order_id).first()
        if not order:
            await update.message.reply_text("❌ الطلب غير موجود.")
            return ConversationHandler.END

        order.code_delivered = code
        order.status = "completed"
        db.commit()

        user = db.query(User).filter_by(id=order.user_id).first()
        await send_notification(context.bot, user.telegram_id, f"🎉 تم إتمام طلبك #{order.id}.\n🔑 الكود: `{code}`")

        await update.message.reply_text("✅ تم حفظ الكود وإشعار المستخدم.", reply_markup=back_to_menu_button())
        return MAIN_MENU
    except Exception as e:
        logger.error(f"Enter code error: {e}", exc_info=True)
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=back_to_menu_button())
        return MAIN_MENU
    finally:
        db.close()

# ---------- Fallback Cancel ----------
@admin_only
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 تم إلغاء العملية.", reply_markup=back_to_menu_button())
    return MAIN_MENU

# ---------- Conversation Handler ----------
def admin_conversation_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("admin", admin_start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(admin_menu_callback, pattern="^admin_"),
            ],
            VIEW_ORDERS: [
                CallbackQueryHandler(view_order_detail, pattern="^admin_order_"),
                CallbackQueryHandler(admin_menu_callback, pattern="^admin_back_to_menu$"),
            ],
            VIEW_DEPOSITS: [
                CallbackQueryHandler(view_deposit_detail, pattern="^admin_deposit_"),
                CallbackQueryHandler(admin_menu_callback, pattern="^admin_back_to_menu$"),
            ],
            ORDER_DETAIL: [
                CallbackQueryHandler(order_action, pattern="^admin_order_(complete|cancel|add_code)$"),
                CallbackQueryHandler(admin_menu_callback, pattern="^admin_back_to_orders$"),
                CallbackQueryHandler(admin_menu_callback, pattern="^admin_back_to_menu$"),
            ],
            DEPOSIT_DETAIL: [
                CallbackQueryHandler(deposit_action, pattern="^admin_deposit_(approve|reject)$"),
                CallbackQueryHandler(admin_menu_callback, pattern="^admin_back_to_deposits$"),
                CallbackQueryHandler(admin_menu_callback, pattern="^admin_back_to_menu$"),
            ],
            ENTER_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order_code),
            ],
            BOT_CONTROL: [
                CallbackQueryHandler(bot_control_action, pattern="^admin_(start_bot|stop_bot|bot_status|back_to_menu)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(admin_menu_callback, pattern="^admin_exit$"),
            CallbackQueryHandler(admin_menu_callback, pattern="^admin_back_to_menu$"),
        ],
        allow_reentry=True,
    )