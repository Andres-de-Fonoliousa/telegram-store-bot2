import json
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from core.database import SessionLocal
from core.models import User, Order, DepositOrder, Product, ProductsPrice, ExchangeRate
from core.notifications import send_notification
from config import settings
import main

logger = logging.getLogger(__name__)

# States (added new ones)
(
    MAIN_MENU,
    VIEW_ORDERS,
    VIEW_DEPOSITS,
    ORDER_DETAIL,
    DEPOSIT_DETAIL,
    ENTER_CODE,
    BOT_CONTROL,
    PROFIT_START_DATE,
    PROFIT_END_DATE,
    PRICE_MENU,
    ADD_PRICE,
    EXCHANGE_RATE_MENU,      # جديد
    SET_EXCHANGE_RATE,       # جديد
) = range(13)

ADMIN_IDS = settings.ADMIN_IDS



# ---------- Helpers ----------
def get_current_week_range():
    """حساب بداية ونهاية الأسبوع المالي الحالي بناءً على أيام (1,8,15,22,29) من الشهر."""
    today = datetime.today()
    start_days = [1, 8, 15, 22, 29]

    current_start_day = None
    for day in reversed(start_days):
        try:
            candidate = today.replace(day=day)
            if candidate <= today:
                current_start_day = day
                break
        except ValueError:
            continue

    if current_start_day is None:
        current_start_day = 1

    start_date = today.replace(day=current_start_day)

    current_index = start_days.index(current_start_day)
    if current_index == len(start_days) - 1:
        if today.month == 12:
            end_date = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    else:
        next_start_day = start_days[current_index + 1]
        end_date = today.replace(day=next_start_day) - timedelta(days=1)

    return start_date, end_date

async def show_weekly_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض أرباح الأسبوع الحالي بشكل تلقائي."""
    query = update.callback_query
    await query.answer()

    start_date, end_date = get_current_week_range()

    db = SessionLocal()
    try:
        orders = db.query(Order).filter(
            Order.status == "completed",
            Order.created_at >= start_date,
            Order.created_at <= end_date
        ).all()

        total_revenue = 0
        total_cost = 0
        for order in orders:
            total_revenue += order.total_price_syp
            qty = order.quantity or 1
            unit_record = db.query(ProductsPrice).filter_by(
                product_id=order.product_id, option_value='unit'
            ).first()
            if unit_record and unit_record.provider_cost:
                total_cost += unit_record.provider_cost * qty
                continue
            answers = json.loads(order.field_answers) if order.field_answers else {}
            option_val = answers.get("uc_amount") or answers.get("diamond_amount") or answers.get(
                "amount") or answers.get("quantity") or answers.get("membership_type")
            option_val = str(option_val) if option_val else None
            if option_val:
                price_record = db.query(ProductsPrice).filter_by(
                    product_id=order.product_id, option_value=option_val
                ).first()
                if price_record and price_record.provider_cost:
                    total_cost += price_record.provider_cost * qty

        profit = total_revenue - total_cost
        summary = (
            f"📊 *تقرير أرباح الأسبوع*\n"
            f"🗓️ الفترة: {start_date.strftime('%Y-%m-%d')} ➔ {end_date.strftime('%Y-%m-%d')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💵 إجمالي المبيعات: {total_revenue} ل.س\n"
            f"📦 إجمالي التكلفة: {total_cost} ل.س\n"
            f"💰 صافي الربح: {profit} ل.س"
        )
        await query.edit_message_text(summary, parse_mode="Markdown", reply_markup=back_to_menu_button())
    finally:
        db.close()
    return MAIN_MENU

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

# ---------- Exchange Rate Management ----------
@admin_only
async def exchange_rate_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض سعر الصرف الحالي مع خيار التحديث."""
    query = update.callback_query
    await query.answer()

    db = SessionLocal()
    try:
        # جلب آخر سعر صرف مسجل
        latest_rate = db.query(ExchangeRate).order_by(ExchangeRate.effective_date.desc()).first()
        if latest_rate:
            rate_text = f"السعر الحالي: **{latest_rate.rate}** ل.س لكل 1 دولار\n(آخر تحديث: {latest_rate.effective_date.strftime('%Y-%m-%d %H:%M')})"
        else:
            rate_text = "لم يتم تحديد سعر صرف بعد."

        buttons = [
            [InlineKeyboardButton("🔄 تحديث سعر الصرف", callback_data="admin_set_exchange_rate")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="admin_back_to_menu")],
        ]
        await query.edit_message_text(
            f"💱 *إدارة سعر الصرف*\n\n{rate_text}",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        return EXCHANGE_RATE_MENU
    finally:
        db.close()

@admin_only
async def set_exchange_rate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب إدخال سعر الصرف الجديد."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📝 أرسل سعر الصرف الجديد (عدد صحيح: ل.س لكل 1 دولار):\n"
        "مثال: `14000`",
        parse_mode="Markdown"
    )
    return SET_EXCHANGE_RATE

async def receive_exchange_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال الرقم وحفظه."""
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح فقط.")
        return SET_EXCHANGE_RATE

    new_rate = int(text)
    admin_id = update.effective_user.id

    db = SessionLocal()
    try:
        # إنشاء سجل جديد
        rate_entry = ExchangeRate(
            rate=new_rate,
            created_by_admin_id=admin_id
        )
        db.add(rate_entry)
        db.commit()

        await update.message.reply_text(
            f"✅ تم تحديث سعر الصرف إلى **{new_rate}** ل.س لكل دولار.",
            parse_mode="Markdown",
            reply_markup=back_to_menu_button()
        )
        return MAIN_MENU
    except Exception as e:
        logger.error(f"Error saving exchange rate: {e}", exc_info=True)
        await update.message.reply_text("❌ حدث خطأ أثناء الحفظ.", reply_markup=back_to_menu_button())
        return MAIN_MENU
    finally:
        db.close()

def build_admin_menu_keyboard():
    buttons = [
        [InlineKeyboardButton("📦 الطلبات المعلقة", callback_data="admin_orders_pending")],
        [InlineKeyboardButton("📋 جميع الطلبات", callback_data="admin_orders_all")],
        [InlineKeyboardButton("💰 طلبات الشحن المعلقة", callback_data="admin_deposits_pending")],
        [InlineKeyboardButton("💵 جميع طلبات الشحن", callback_data="admin_deposits_all")],
        [InlineKeyboardButton("💲 إدارة الأسعار", callback_data="admin_price_menu")],
        [InlineKeyboardButton("💱 سعر الصرف", callback_data="admin_exchange_rate")],
        [InlineKeyboardButton("📊 تقرير الأرباح", callback_data="admin_profit_start")],
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

    if data == "admin_exchange_rate":
        return await exchange_rate_menu(update, context)

    if data == "admin_bot_control":
        return await bot_control_menu(update, context)

    if data == "admin_profit_start":
        return await show_weekly_profit(update, context)

    if data == "admin_price_menu":
        return await price_menu(update, context)

    db = SessionLocal()
    try:
        if data == "admin_orders_pending":
            orders = db.query(Order).filter(Order.status.in_(["pending", "pending_payment"])).order_by(Order.id.desc()).limit(10).all()
            return await show_orders_list(query, orders, "الطلبات المعلقة", db)

        elif data == "admin_orders_all":
            orders = db.query(Order).order_by(Order.id.desc()).limit(10).all()
            return await show_orders_list(query, orders, "جميع الطلبات", db)

        elif data == "admin_deposits_pending":
            deposits = db.query(DepositOrder).filter(DepositOrder.status.in_(["pending", "pending_payment"])).order_by(DepositOrder.id.desc()).limit(10).all()
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
        return await bot_control_menu(update, context)

    elif data == "admin_back_to_menu":
        await query.edit_message_text("🔐 لوحة التحكم:", reply_markup=build_admin_menu_keyboard())
        return MAIN_MENU

    return MAIN_MENU

# ---------- Profit Report ----------
async def receive_profit_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        start = datetime.strptime(text, "%Y-%m-%d")
        context.user_data["profit_start"] = start
        await update.message.reply_text("📅 أدخل تاريخ النهاية (YYYY-MM-DD):")
        return PROFIT_END_DATE
    except ValueError:
        await update.message.reply_text("❌ تنسيق خاطئ. استخدم YYYY-MM-DD:")
        return PROFIT_START_DATE

async def receive_profit_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        end = datetime.strptime(text, "%Y-%m-%d")
        start = context.user_data["profit_start"]
        if end < start:
            await update.message.reply_text("❌ تاريخ النهاية قبل البداية. أعد إدخال النهاية:")
            return PROFIT_END_DATE

        db = SessionLocal()
        try:
            orders = db.query(Order).filter(
                Order.status == "completed",
                Order.created_at >= start,
                Order.created_at <= end
            ).all()

            total_revenue = 0
            total_cost = 0
            for order in orders:
                total_revenue += order.total_price_syp
                qty = order.quantity or 1
                unit_record = db.query(ProductsPrice).filter_by(
                    product_id=order.product_id, option_value='unit'
                ).first()
                if unit_record and unit_record.provider_cost:
                    total_cost += unit_record.provider_cost * qty
                    continue
                answers = json.loads(order.field_answers) if order.field_answers else {}
                option_val = answers.get("uc_amount") or answers.get("diamond_amount") or answers.get("amount") or answers.get("quantity") or answers.get("membership_type")
                option_val = str(option_val) if option_val else None
                if option_val:
                    price_record = db.query(ProductsPrice).filter_by(
                        product_id=order.product_id, option_value=option_val
                    ).first()
                    if price_record and price_record.provider_cost:
                        total_cost += price_record.provider_cost * qty

            profit = total_revenue - total_cost
            summary = (
                f"📊 *تقرير الأرباح*\n"
                f"من: {start.strftime('%Y-%m-%d')} إلى: {end.strftime('%Y-%m-%d')}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💵 إجمالي المبيعات: {total_revenue} ل.س\n"
                f"📦 إجمالي التكلفة: {total_cost} ل.س\n"
                f"💰 صافي الربح: {profit} ل.س"
            )
            await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=back_to_menu_button())
        finally:
            db.close()
        return MAIN_MENU
    except ValueError:
        await update.message.reply_text("❌ تنسيق خاطئ. استخدم YYYY-MM-DD:")
        return PROFIT_END_DATE

# ---------- Price Management ----------
async def price_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = SessionLocal()
    try:
        prices = db.query(ProductsPrice).all()
        text = "💲 *قائمة الأسعار الحالية*\n\n"
        if not prices:
            text += "لا توجد أسعار مضافة."
        for p in prices:
            product = db.query(Product).filter_by(id=p.product_id).first()
            product_name = product.name_ar if product else f"منتج #{p.product_id}"
            text += f"🔹 {product_name} | خيار: {p.option_value} | سعر: {p.price_syp} ل.س | تكلفة: {p.provider_cost}\n"
        buttons = [
            [InlineKeyboardButton("➕ إضافة سعر", callback_data="admin_add_price")],
            [InlineKeyboardButton("✏️ تعديل سعر", callback_data="admin_edit_price")],
            [InlineKeyboardButton("🗑️ حذف سعر", callback_data="admin_delete_price")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="admin_back_to_menu")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return PRICE_MENU
    finally:
        db.close()

@admin_only
async def add_price_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ أدخل بيانات السعر الجديد بالشكل:\n`product_id option_value price_syp provider_cost`\nمثال: `1 60 50000 40000`",
        parse_mode="Markdown"
    )
    return ADD_PRICE

async def add_price_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split()
    if len(parts) != 4:
        await update.message.reply_text("❌ خطأ. أعد المحاولة بنفس التنسيق.")
        return ADD_PRICE
    product_id, option_value, price_syp, cost = parts
    db = SessionLocal()
    try:
        new_price = ProductsPrice(
            product_id=int(product_id),
            option_value=option_value,
            price_syp=int(price_syp),
            provider_cost=int(cost) if cost else None
        )
        db.add(new_price)
        db.commit()
        await update.message.reply_text("✅ تمت إضافة السعر بنجاح.", reply_markup=back_to_menu_button())
    except Exception as e:
        await update.message.reply_text(f"❌ فشلت الإضافة: {e}", reply_markup=back_to_menu_button())
    finally:
        db.close()
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
        if deposit.status in ["pending", "pending_payment"]:
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
            refunded_amount = 0
            if order.status != "cancelled" and not order.refunded and user:
                user.balance += order.total_price_syp
                order.refunded = True
                refunded_amount = order.total_price_syp
            order.status = "cancelled"
            db.commit()
            if user:
                if refunded_amount:
                    await send_notification(
                        context.bot, user.telegram_id,
                        f"❌ تم إلغاء طلبك #{order.id}.\n"
                        f"💸 تم إرجاع {refunded_amount} ل.س إلى رصيدك."
                    )
                else:
                    await send_notification(context.bot, user.telegram_id, f"❌ تم إلغاء طلبك #{order.id}.")
            await query.edit_message_text(
                f"❌ تم إلغاء الطلب #{order.id}."
                + (f" وتم إرجاع {refunded_amount} ل.س للعميل." if refunded_amount else ""),
                reply_markup=back_to_menu_button()
            )
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
            if deposit.status in ["pending", "pending_payment"] and not deposit.balance_credited:
                deposit.status = "approved"
                user.balance += deposit.amount
                deposit.balance_credited = True
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
            PROFIT_START_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_profit_start_date),
            ],
            PROFIT_END_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_profit_end_date),
            ],
            PRICE_MENU: [
                CallbackQueryHandler(add_price_start, pattern="^admin_add_price$"),
                CallbackQueryHandler(admin_menu_callback, pattern="^admin_back_to_menu$"),
            ],
            ADD_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_price_execute),
            ],
            EXCHANGE_RATE_MENU: [
                CallbackQueryHandler(set_exchange_rate_start, pattern="^admin_set_exchange_rate$"),
                CallbackQueryHandler(admin_menu_callback, pattern="^admin_back_to_menu$"),
            ],
            SET_EXCHANGE_RATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_exchange_rate),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(admin_menu_callback, pattern="^admin_exit$"),
            CallbackQueryHandler(admin_menu_callback, pattern="^admin_back_to_menu$"),
        ],
        allow_reentry=True,
    )