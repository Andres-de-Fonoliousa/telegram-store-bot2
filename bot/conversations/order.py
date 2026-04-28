# order.py – النسخة النهائية الكاملة مع دعم الرجوع ومعالجة أزرار القائمة
import os
import re
import json
import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, CallbackContext
)
from core.database import SessionLocal
from core.models import User, Order, Product, ProductsPrice, ExchangeRate
from services.mhd_api import MHDStoreAPI, MHDAPIError
from config import settings
from core.notifications import send_notification

logger = logging.getLogger(__name__)

PRODUCTS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'products.json')

SELECTING_FIELD, CONFIRM_ORDER = range(2)

_products_cache = None


def load_products():
    global _products_cache
    if _products_cache is None:
        with open(PRODUCTS_PATH, encoding="utf-8") as f:
            _products_cache = json.load(f)
    return _products_cache


def get_product_by_id(product_id):
    data = load_products()
    for prod in data["products"]:
        if prod["id"] == product_id:
            return prod
    return None


def get_field_question(field_name):
    questions = {
        "player_id": "🆔 يرجى تزويدنا بـ Player ID الخاص بك:",
        "uc_amount": "💰 ما هي كمية UC المطلوبة؟",
        "diamond_amount": "💎 ما هي كمية الدايموندز المطلوبة؟",
        "amount": "🔢 يرجى تحديد الكمية المطلوبة:",
        "zone_id": "🌍 يرجى تزويدنا بـ Zone ID:",
        "membership_type": "📅 هل ترغب بعضوية أسبوعية أم شهرية؟",
        "quantity": "🔢 يرجى تحديد العدد المطلوب (يجب أن يكون من مضاعفات 100):",
        "profile_url": "🔗 يرجى إرسال رابط الحساب (البروفايل):",
        "before_screenshot": "📸 يرجى إرسال لقطة شاشة للحساب *قبل* البدء (لضمان الخدمة):",
        "post_url": "🔗 يرجى تزويدنا برابط المنشور المطلوب التفاعل معه:",
        "comment_text": "💬 ما هو نص التعليق المطلوب؟",
        "account_url": "🔗 يرجى إرسال رابط الحساب المراد توثيقه:",
        "account_type": "📱 هل الحساب فيسبوك أم إنستغرام؟",
        "budget": "💰 ما هي الميزانية التقريبية للإعلان؟",
        "target_details": "🎯 يرجى تزويدنا بتفاصيل الاستهداف (الدولة، العمر، الاهتمامات):",
        "story_url": "🔗 يرجى إرسال رابط الستوري:",
    }
    return questions.get(field_name, f"📌 {field_name}:")

def escape_markdown(text: str) -> str:
    if not text:
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))


async def notify_admins(bot, text: str):
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception as e:
            logger.warning(f"Failed to notify admin {admin_id}: {e}")


def get_latest_exchange_rate(db):
    rate_obj = db.query(ExchangeRate).order_by(ExchangeRate.effective_date.desc()).first()
    if rate_obj:
        return rate_obj.rate
    return 10000

def get_unit_price_record(db, product_id: int):
    """ترجع سجل سعر الوحدة إن وجد للمنتج (option_value = 'unit')"""
    return db.query(ProductsPrice).filter_by(
        product_id=product_id,
        option_value='unit'
    ).first()

# -------------------- دوال الشراء الآلي --------------------
async def process_auto_purchase(order_id: int, user_id: int, bot, context: CallbackContext):
    db = SessionLocal()
    try:
        order = db.query(Order).filter_by(id=order_id).first()
        if not order:
            logger.error(f"Order {order_id} not found for auto purchase")
            return

        product = db.query(Product).filter_by(id=order.product_id).first()
        user = db.query(User).filter_by(id=user_id).first()

        if not product or not user:
            logger.error(f"Product or user missing for order {order_id}")
            return

        answers = json.loads(order.field_answers) if order.field_answers else {}

        # --- دالة مساعدة لاستخراج player_id و zone_id من الإجابات ---
        def extract_player_and_zone(answers: dict):
            player_id = answers.get("player_id")
            if not player_id:
                player_id = answers.get("id")  # دعم حقل اسمه 'id'

            zone_id = answers.get("zone_id")  # حقل منفصل

            # إذا لم يوجد player_id، نستخدم zone_id (للمنتجات التي تعتمد عليه فقط)
            if not player_id:
                player_id = zone_id
                zone_id = None  # حتى لا نرسله مرتين
            # إذا لم يوجد شيء، نبحث عن أول قيمة نصية
            if not player_id:
                for val in answers.values():
                    if isinstance(val, str) and val.strip():
                        player_id = val
                        break
            if not player_id:
                player_id = "no_player_id"

            extra = {}
            if zone_id:  # إذا كان هناك zone_id منفصل وكان لدينا player_id مختلف
                extra["zoneId"] = zone_id
            return player_id, extra

        # --- المنتجات ذات الكمية المفتوحة ---
        unit_price_record = db.query(ProductsPrice).filter_by(
            product_id=product.id,
            option_value='unit'
        ).first()

        if unit_price_record is not None:
            quantity_str = answers.get("quantity")
            if not quantity_str:
                raise ValueError("Could not determine quantity from answers")
            try:
                quantity = int(quantity_str)
            except ValueError:
                raise ValueError("Quantity is not a valid integer")

            if not product.external_product_id:
                raise ValueError(f"Product {product.id} has no external_product_id set")

            player_id, extra_params = extract_player_and_zone(answers)

            api = MHDStoreAPI(settings.MHD_API_KEY, settings.MHD_API_BASE_URL)
            idempotency_key = f"tg_order_{order_id}"

            try:
                external_uuid = api.create_order(
                    product_id=product.external_product_id,
                    player_id=player_id,
                    quantity=quantity,
                    idempotency_key=idempotency_key,
                    extra_params=extra_params if extra_params else None
                )
            except MHDAPIError as e:
                logger.error(f"Auto purchase failed for order {order_id}: {e}")
                order.status = "auto_failed"
                order.auto_purchase_attempted = True
                user_locked = db.query(User).filter_by(id=user.id).with_for_update().first()
                user_locked.balance += order.total_price_syp
                db.commit()
                await send_notification(
                    bot, user.telegram_id,
                    f"❌ عذراً، فشلت عملية الشراء الآلي للطلب #{order.id}.\n"
                    f"تم إرجاع {order.total_price_syp} ل.س إلى رصيدك.\n"
                    f"يرجى المحاولة لاحقاً أو التواصل مع الدعم."
                )
                await notify_admins(
                    bot,
                    f"⚠️ فشل شراء آلي للطلب #{order.id}\n"
                    f"المنتج: {product.name_ar}\n"
                    f"المستخدم: @{user.username or user.first_name}\n"
                    f"الخطأ: {e}"
                )
                return

            # نجاح
            order.external_order_uuid = external_uuid
            order.status = "processing"
            order.auto_purchase_attempted = True
            db.commit()
            await send_notification(
                bot, user.telegram_id,
                f"🔄 تم استلام طلبك #{order.id} وجاري تنفيذه آلياً...\n"
                f"سيتم إعلامك فور اكتماله."
            )
            await notify_admins(
                bot,
                f"🤖 بدء شراء آلي للطلب #{order.id}\n"
                f"المنتج: {product.name_ar}\n"
                f"معرف MHD: {external_uuid}"
            )
            context.job_queue.run_once(
                poll_order_status,
                when=5,
                data={"order_id": order_id, "attempt": 1, "started_at": datetime.utcnow().isoformat()},
                name=f"poll_order_{order_id}"
            )
            return

        # --- المنتجات التقليدية متعددة الخيارات ---
        option_value = None
        for key in ["uc_amount", "diamond_amount", "amount", "quantity", "membership_type"]:
            if key in answers:
                option_value = answers[key]
                break
        if option_value is None:
            option_value = next(iter(answers.values())) if answers else None

        if option_value is None:
            raise ValueError("Could not determine option_value from order answers")

        price_record = db.query(ProductsPrice).filter_by(
            product_id=order.product_id,
            option_value=str(option_value)
        ).first()

        if not price_record or not price_record.external_product_id:
            raise ValueError(f"No external_product_id found for product {order.product_id} option {option_value}")

        external_product_id = price_record.external_product_id
        player_id, extra_params = extract_player_and_zone(answers)

        if not settings.MHD_API_ENABLED or not settings.MHD_API_KEY:
            logger.warning("MHD API disabled or key missing, skipping auto purchase")
            return

        api = MHDStoreAPI(settings.MHD_API_KEY, settings.MHD_API_BASE_URL)

        # فحص رصيد MHD
        exchange_rate = get_latest_exchange_rate(db)
        order_cost_usd = 0.0
        if price_record.provider_cost is not None and exchange_rate > 0:
            order_cost_usd = price_record.provider_cost / exchange_rate

        mhd_balance = api.get_balance_usd()

        if mhd_balance is not None and order_cost_usd > 0 and mhd_balance < order_cost_usd:
            logger.warning(
                f"Insufficient MHD balance for order {order_id}: need ${order_cost_usd:.2f}, have ${mhd_balance:.2f}")
            order.status = "auto_failed_balance"
            order.auto_purchase_attempted = True
            user_locked = db.query(User).filter_by(id=user.id).with_for_update().first()
            user_locked.balance += order.total_price_syp
            db.commit()

            await send_notification(
                bot, user.telegram_id,
                f"❌ عذراً، لا يمكن تنفيذ طلبك #{order.id} حالياً بسبب مشكلة فنية.\n"
                f"تم إرجاع {order.total_price_syp} ل.س إلى رصيدك.\n"
                f"يرجى المحاولة لاحقاً أو التواصل مع الدعم."
            )
            await notify_admins(
                bot,
                f"⚠️ فشل شراء آلي (رصيد MHD غير كافٍ) للطلب #{order.id}\n"
                f"المنتج: {product.name_ar}\n"
                f"المستخدم: @{user.username or user.first_name}\n"
                f"التكلفة المطلوبة: ${order_cost_usd:.2f}\n"
                f"الرصيد الحالي: ${mhd_balance:.2f}"
            )
            return

        idempotency_key = f"tg_order_{order_id}"

        try:
            external_uuid = api.create_order(
                product_id=external_product_id,
                player_id=player_id,
                quantity=1,
                idempotency_key=idempotency_key,
                extra_params=extra_params if extra_params else None
            )
        except MHDAPIError as e:
            logger.error(f"Auto purchase failed for order {order_id}: {e}")
            order.status = "auto_failed"
            order.auto_purchase_attempted = True
            user_locked = db.query(User).filter_by(id=user.id).with_for_update().first()
            user_locked.balance += order.total_price_syp
            db.commit()

            await send_notification(
                bot, user.telegram_id,
                f"❌ عذراً، فشلت عملية الشراء الآلي للطلب #{order.id}.\n"
                f"تم إرجاع {order.total_price_syp} ل.س إلى رصيدك.\n"
                f"يرجى المحاولة لاحقاً أو التواصل مع الدعم."
            )
            await notify_admins(
                bot,
                f"⚠️ فشل شراء آلي للطلب #{order.id}\n"
                f"المنتج: {product.name_ar}\n"
                f"المستخدم: @{user.username or user.first_name}\n"
                f"الخطأ: {e}"
            )
            return

        # نجح الطلب الخارجي
        order.external_order_uuid = external_uuid
        order.status = "processing"
        order.auto_purchase_attempted = True
        db.commit()

        await send_notification(
            bot, user.telegram_id,
            f"🔄 تم استلام طلبك #{order.id} وجاري تنفيذه آلياً...\n"
            f"سيتم إعلامك فور اكتماله."
        )
        await notify_admins(
            bot,
            f"🤖 بدء شراء آلي للطلب #{order.id}\n"
            f"المنتج: {product.name_ar}\n"
            f"معرف MHD: {external_uuid}"
        )

        context.job_queue.run_once(
            poll_order_status,
            when=5,
            data={"order_id": order_id, "attempt": 1, "started_at": datetime.utcnow().isoformat()},
            name=f"poll_order_{order_id}"
        )

    except Exception as e:
        logger.error(f"Unexpected error in process_auto_purchase: {e}", exc_info=True)
        try:
            order = db.query(Order).filter_by(id=order_id).first()
            user = db.query(User).filter_by(id=user_id).first()
            if order and order.status == "pending":
                order.status = "auto_failed"
                order.auto_purchase_attempted = True
                user_locked = db.query(User).filter_by(id=user_id).with_for_update().first()
                user_locked.balance += order.total_price_syp
                db.commit()
                await send_notification(
                    bot, user.telegram_id,
                    f"❌ حدث خطأ غير متوقع أثناء الشراء الآلي للطلب #{order.id}.\n"
                    f"تم إرجاع رصيدك. يرجى التواصل مع الدعم."
                )
                await notify_admins(
                    bot,
                    f"🔥 خطأ غير متوقع في process_auto_purchase للطلب #{order.id}\n"
                    f"الخطأ: {str(e)[:200]}"
                )
        except Exception as inner_e:
            logger.error(f"Failed to rollback after error: {inner_e}")
    finally:
        db.close()

async def poll_order_status(context: CallbackContext):
    job_data = context.job.data
    order_id = job_data["order_id"]
    attempt = job_data["attempt"]
    started_at_str = job_data.get("started_at")
    started_at = datetime.fromisoformat(started_at_str) if started_at_str else datetime.utcnow()

    db = SessionLocal()
    try:
        order = db.query(Order).filter_by(id=order_id).first()
        if not order or order.status != "processing":
            return

        if not order.external_order_uuid:
            logger.error(f"Order {order_id} missing external_order_uuid")
            return

        api = MHDStoreAPI(settings.MHD_API_KEY, settings.MHD_API_BASE_URL)

        try:
            status_data = api.get_order_status(order.external_order_uuid)
            mhd_status = status_data.get("status")
        except MHDAPIError as e:
            logger.warning(f"Poll attempt {attempt} failed: {e}")
            elapsed = (datetime.utcnow() - started_at).total_seconds()
            if elapsed < 120:
                next_delay = min(attempt * 2, 15)
                context.job_queue.run_once(
                    poll_order_status,
                    when=next_delay,
                    data={"order_id": order_id, "attempt": attempt + 1, "started_at": started_at_str},
                    name=f"poll_order_{order_id}"
                )
            else:
                order.status = "auto_timeout"
                db.commit()
                await notify_admins(
                    context.bot,
                    f"⏰ انتهت مهلة استطلاع الطلب #{order.id} (MHD: {order.external_order_uuid})\n"
                    f"يحتاج تدخل يدوي."
                )
            return

        user = db.query(User).filter_by(id=order.user_id).first()

        if mhd_status == "completed":
            order.status = "completed"
            delivered = status_data.get("delivered_data", "")
            order.code_delivered = delivered
            db.commit()

            escaped_delivered = escape_markdown(delivered)
            await send_notification(
                context.bot, user.telegram_id,
                f"🎉 تم إتمام طلبك #{order.id} بنجاح!\n\n"
                f"🔑 البيانات: `{escaped_delivered}`\n\n"
                f"شكراً لاستخدامك خدماتنا.",
                parse_mode="Markdown"
            )
            await notify_admins(
                context.bot,
                f"✅ اكتمل الطلب الآلي #{order.id}\n"
                f"MHD UUID: {order.external_order_uuid}\n"
                f"البيانات: {delivered}"
            )

        elif mhd_status in ("failed", "cancelled"):
            order.status = "auto_failed"
            db.commit()
            user_locked = db.query(User).filter_by(id=order.user_id).with_for_update().first()
            user_locked.balance += order.total_price_syp
            db.commit()

            await send_notification(
                context.bot, user.telegram_id,
                f"❌ فشل تنفيذ طلبك #{order.id}.\n"
                f"تم إرجاع {order.total_price_syp} ل.س إلى رصيدك.\n"
                f"يرجى المحاولة لاحقاً أو التواصل مع الدعم."
            )
            await notify_admins(
                context.bot,
                f"❌ فشل الطلب الآلي #{order.id} (حالة MHD: {mhd_status})\n"
                f"MHD UUID: {order.external_order_uuid}"
            )

        else:
            elapsed = (datetime.utcnow() - started_at).total_seconds()
            if elapsed < 120:
                next_delay = min(attempt * 2, 15)
                context.job_queue.run_once(
                    poll_order_status,
                    when=next_delay,
                    data={"order_id": order_id, "attempt": attempt + 1, "started_at": started_at_str},
                    name=f"poll_order_{order_id}"
                )
            else:
                order.status = "auto_timeout"
                db.commit()
                await notify_admins(
                    context.bot,
                    f"⏰ انتهت مهلة استطلاع الطلب #{order.id} - يحتاج تدخل يدوي."
                )

    except Exception as e:
        logger.error(f"Error in poll_order_status: {e}", exc_info=True)
    finally:
        db.close()


async def exit_to_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إنهاء المحادثة والعودة إلى قائمة الأقسام."""
    context.user_data.clear()
    await update.message.reply_text("🔙 تم إلغاء العملية والعودة إلى الأقسام.")
    from bot.handlers.user import show_categories
    await show_categories(update, context)
    return ConversationHandler.END


# -------------------- دوال المحادثة (مع دعم الرجوع) --------------------
async def start_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.replace("product_", ""))
    product = get_product_by_id(product_id)
    if not product:
        await query.edit_message_text("❌ نعتذر، هذا المنتج غير متوفر حالياً. يرجى اختيار منتج آخر.")
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["product_id"] = product["id"]
    context.user_data["product_name"] = product["name_ar"]
    context.user_data["validation"] = product.get("validation", {})
    context.user_data["answers"] = {}

    fields = product.get("fields", [])
    validation = product.get("validation", {})
    main_choice_field = None
    for f in fields:
        if validation.get(f, {}).get("type") == "choice":
            main_choice_field = f
            break
    if not main_choice_field:
        for fallback in ["quantity", "amount"]:
            if fallback in fields:
                main_choice_field = fallback
                break
    if not main_choice_field and fields:
        main_choice_field = fields[0]
    reordered_fields = [main_choice_field] + [f for f in fields if f != main_choice_field] if main_choice_field else fields
    context.user_data["fields"] = reordered_fields
    context.user_data["current_field_index"] = 0

    await query.edit_message_text(
        f"🛒 شكرًا لاختيارك *{escape_markdown(product['name_ar'])}*. سنقوم الآن بإعداد طلبك. يرجى الإجابة على بعض الأسئلة لإتمام العملية.",
        parse_mode="Markdown"
    )
    return await ask_next_field(update, context)


async def ask_next_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fields = context.user_data["fields"]
    idx = context.user_data["current_field_index"]
    if idx >= len(fields):
        return await show_order_summary(update, context)

    field_name = fields[idx]
    validation = context.user_data["validation"].get(field_name, {})
    question_text = get_field_question(field_name)

    buttons = []
    if validation.get("type") == "choice":
        options = validation.get("options", [])
        for opt in options:
            buttons.append([InlineKeyboardButton(str(opt), callback_data=f"answer_{opt}")])

    # صف أزرار التحكم (رجوع / إلغاء)
    control_row = []
    if idx > 0:
        control_row.append(InlineKeyboardButton("🔙 رجوع", callback_data="answer_back"))
    control_row.append(InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order"))
    buttons.append(control_row)

    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    if update.callback_query:
        await update.callback_query.edit_message_text(question_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(question_text, reply_markup=reply_markup)

    return SELECTING_FIELD


async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fields = context.user_data["fields"]
    idx = context.user_data["current_field_index"]
    field_name = fields[idx]
    validation = context.user_data["validation"].get(field_name, {})

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "cancel_order":
            await query.edit_message_text("❌ تم إلغاء الطلب. نأمل خدمتك في وقت لاحق.")
            return ConversationHandler.END

        if data == "answer_back":
            if idx > 0:
                context.user_data["current_field_index"] -= 1
                prev_field = fields[idx - 1]
                if prev_field in context.user_data["answers"]:
                    del context.user_data["answers"][prev_field]
            return await ask_next_field(update, context)

        if data.startswith("answer_"):
            answer = data.replace("answer_", "")
        else:
            answer = data
    else:
        if update.message.photo:
            photo = update.message.photo[-1]
            answer = photo.file_id
        else:
            answer = update.message.text

    # ========== قسم التحقق من الصحة ==========
    error_msg = None
    vtype = validation.get("type")

    if vtype == "integer":
        try:
            int_val = int(answer)
            if int_val <= 0:
                error_msg = "❌ يجب أن يكون الرقم موجباً (أكبر من صفر)."
        except ValueError:
            error_msg = "❌ يرجى إدخال رقم صحيح فقط (بدون أحرف أو رموز)."

    elif vtype == "multiple_of":
        try:
            qty = int(answer)
            multiple = validation.get("value", 100)
            if qty % multiple != 0:
                error_msg = f"❌ يجب أن يكون العدد من مضاعفات {multiple}. يرجى المحاولة مرة أخرى."
        except ValueError:
            error_msg = "❌ يرجى إدخال رقم صحيح."

    elif vtype == "min":
        try:
            qty = int(answer)
            min_val = validation.get("value", 1)
            if qty < min_val:
                error_msg = f"❌ الحد الأدنى للكمية هو {min_val}."
        except ValueError:
            error_msg = "❌ يرجى إدخال رقم صحيح."

    if error_msg:
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return await ask_next_field(update, context)

    # تخزين الإجابة
    context.user_data["answers"][field_name] = answer
    context.user_data["current_field_index"] += 1

    field_display = {
        "player_id": "الـ ID", "uc_amount": "كمية الـ UC", "diamond_amount": "كمية الدايموندز",
        "amount": "الكمية", "zone_id": "Zone ID", "membership_type": "نوع العضوية",
        "quantity": "الكمية", "profile_url": "الرابط", "before_screenshot": "لقطة الشاشة",
        "post_url": "رابط المنشور", "comment_text": "نص التعليق", "account_url": "رابط الحساب",
        "account_type": "نوع الحساب", "budget": "الميزانية", "target_details": "تفاصيل الاستهداف",
        "story_url": "رابط الستوري",
    }.get(field_name, field_name)

    if update.message and field_name != "before_screenshot":
        await update.message.reply_text(f"✅ تم استلام *{escape_markdown(field_display)}*. شكرًا لتعاونك.", parse_mode="Markdown")

    return await ask_next_field(update, context)

async def show_order_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        product_name = context.user_data["product_name"]
        answers = context.user_data["answers"]
        product_id = context.user_data["product_id"]

        # ---- تحديد الخيار الرئيسي (كمية أو قيمة الخيار) ----
        main_option = None
        for key in ["uc_amount", "diamond_amount", "amount", "quantity", "membership_type"]:
            if key in answers:
                main_option = answers[key]
                break
        if not main_option:
            main_option = next(iter(answers.values())) if answers else None

        total_price = None
        price_record = None
        is_markup_based = False   # هل السعر معتمد على التكلفة + الربح؟

        if main_option is not None:
            try:
                option_value = int(main_option)
            except (ValueError, TypeError):
                option_value = str(main_option)

            # ---- 1) البحث عن سجل سعر مباشر (خيارات ثابتة) ----
            price_record = db.query(ProductsPrice).filter_by(
                product_id=product_id, option_value=str(option_value)
            ).first()

            if price_record:
                if price_record.price_syp and price_record.price_syp > 0:
                    total_price = price_record.price_syp
                elif price_record.provider_cost is not None:
                    product_obj = db.query(Product).filter_by(id=product_id).first()
                    markup = product_obj.markup_percentage if product_obj else 0
                    total_price = int(price_record.provider_cost * (1 + markup / 100))
                    is_markup_based = True
            else:
                # ---- 2) منتج كمية مفتوحة (سجل unit) ----
                unit_record = get_unit_price_record(db, product_id)
                if unit_record and unit_record.price_syp:
                    try:
                        qty = int(main_option)
                        total_price = qty * unit_record.price_syp
                        price_record = unit_record
                        # إذا كان لسجل unit تكلفة، يمكن اعتباره markup-based لاحقاً
                        if unit_record.provider_cost is not None:
                            # لكن السعر هنا = qty * unit_price_syp، وليس من التكلفة مباشرة.
                            # إذا أردت تطبيق الخصم على منتجات الكمية المفتوحة أيضاً،
                            # يجب حساب سعر الوحدة من التكلفة والربح. سنفترض أن unit.price_syp
                            # هو السعر النهائي، ويمكننا إعادة حسابه إذا كان markup_based.
                            # لكن للتبسيط: إذا أردت دعم الخصم على هذه المنتجات،
                            # يجب أن يكون حساب السعر الأساسي مشابهاً للخيارات الثابتة.
                            # حالياً نكتفي بعدم تطبيق الخصم عليها،
                            # ويمكن إضافته لاحقاً بسهولة.
                            pass
                    except ValueError:
                        pass

        # ---- جلب المستخدم وتطبيق الخصم (فقط على المنتجات المعتمدة على التكلفة) ----
        user = None
        balance = 0
        if update.effective_user:
            user = db.query(User).filter_by(telegram_id=update.effective_user.id).first()
        elif update.callback_query and update.callback_query.from_user:
            user = db.query(User).filter_by(telegram_id=update.callback_query.from_user.id).first()
        if user:
            balance = user.balance

        original_total_price = None
        discount_applied = 0

        # إعادة حساب السعر مع الخصم إذا كان المستخدم لديه خصم وكان المنتج يعتمد على التكلفة
        if user and user.discount_percent > 0 and price_record and price_record.provider_cost:
            # جلب نسبة الربح الحالية للمنتج (قد تكون 0)
            product_obj = db.query(Product).filter_by(id=product_id).first()
            current_markup = product_obj.markup_percentage if product_obj else 0

            # لا نطبق الخصم إلا إذا كان هناك ربح فعلي (markup > 0)
            if current_markup > 0:
                # السعر الأصلي (بدون خصم) = التكلفة + الربح الكامل
                original_total_price = int(price_record.provider_cost * (1 + current_markup / 100))

                # الربح الفعّال بعد الخصم = الربح الحالي - نسبة الخصم (بحد أدنى 0)
                effective_markup = max(0, current_markup - user.discount_percent)
                total_price = int(price_record.provider_cost * (1 + effective_markup / 100))
                discount_applied = user.discount_percent
            else:
                # لا ربح -> لا خصم
                original_total_price = None
                discount_applied = 0
                # يبقى total_price كما حُسب سابقاً (من price_syp أو السعر اليدوي)
        else:
            original_total_price = None
            discount_applied = 0

        # ---- بناء نص الملخص ----
        summary_lines = [f"🛍️ *مراجعة وتأكيد الطلب - {escape_markdown(product_name)}*"]
        for field, value in answers.items():
            field_display = {
                "player_id": "ID اللاعب", "uc_amount": "UC", "diamond_amount": "دايموندز",
                "amount": "الكمية", "zone_id": "Zone ID", "membership_type": "نوع العضوية",
                "quantity": "الكمية", "profile_url": "الرابط", "before_screenshot": "سكرين قبل",
                "post_url": "رابط المنشور", "comment_text": "نص التعليق", "account_url": "رابط الحساب",
                "account_type": "نوع الحساب", "budget": "الميزانية", "target_details": "تفاصيل",
                "story_url": "رابط الستوري",
            }.get(field, field)
            escaped_value = escape_markdown(str(value))
            summary_lines.append(f"• {field_display}: {escaped_value}")

        if total_price is not None:
            if discount_applied > 0 and original_total_price:
                summary_lines.append(f"💰 *السعر الأصلي:* ~~{original_total_price} ل.س~~")
                summary_lines.append(f"💎 *السعر بعد الخصم ({discount_applied}%):* {total_price} ل.س")
            else:
                summary_lines.append(f"💰 *السعر:* {total_price} ل.س")
            summary_lines.append(f"💵 *رصيدك الحالي:* {balance} ل.س")
        else:
            summary_lines.append("\n❌ لا يوجد سعر محدد لهذا الخيار. لا يمكن إتمام الطلب حالياً.")

        price_missing = total_price is None
        insufficient = total_price is not None and balance < total_price

        if price_missing:
            summary_lines.append("\nيرجى التواصل مع الدعم لتحديث الأسعار.")
            buttons = [
                [InlineKeyboardButton("🔙 تعديل الطلب", callback_data="edit_order")],
                [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")]
            ]
        elif insufficient:
            summary_lines.append("\n❗ *رصيدك غير كافٍ لإتمام الطلب. يرجى شحن رصيدك أولاً.*")
            buttons = [
                [InlineKeyboardButton("💰 شحن الرصيد", callback_data="recharge_balance")],
                [InlineKeyboardButton("🔙 تعديل الطلب", callback_data="edit_order")],
                [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")]
            ]
        else:
            buttons = [
                [InlineKeyboardButton("✅ تأكيد الطلب", callback_data="confirm_order")],
                [InlineKeyboardButton("🔙 تعديل الطلب", callback_data="edit_order")],
                [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="cancel_order")]
            ]

        if update.callback_query:
            await update.callback_query.edit_message_text(
                "\n".join(summary_lines),
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "\n".join(summary_lines),
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown"
            )

        # تخزين السعر النهائي (بعد الخصم) والمعلومات للاستخدام لاحقاً
        context.user_data["total_price"] = total_price
        context.user_data["price_record_id"] = price_record.id if price_record else None
        context.user_data["discount_applied"] = discount_applied
        context.user_data["original_total_price"] = original_total_price
        return CONFIRM_ORDER
    finally:
        db.close()

async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel_order":
        await query.edit_message_text("❌ تم إلغاء الطلب. نأمل خدمتك في وقت لاحق.")
        return ConversationHandler.END

    if data == "edit_order":
        context.user_data["current_field_index"] = 0
        context.user_data["answers"] = {}
        await query.edit_message_text("🔁 سيتم إعادة الأسئلة من البداية. يرجى الإجابة من جديد.")
        return await ask_next_field(update, context)

    if data == "recharge_balance":
        await query.edit_message_text(
            "💰 يرجى استخدام زر 'شحن الرصيد' في القائمة الرئيسية لإضافة رصيد إلى حسابك، ثم العودة لإتمام الطلب."
        )
        return ConversationHandler.END

    db = SessionLocal()
    try:
        telegram_user = query.from_user
        user = db.query(User).filter_by(telegram_id=telegram_user.id).with_for_update().first()
        if not user:
            user = User(
                telegram_id=telegram_user.id,
                username=telegram_user.username,
                first_name=telegram_user.first_name,
                last_name=telegram_user.last_name,
                balance=0
            )
            db.add(user)
            db.commit()
            user = db.query(User).filter_by(telegram_id=telegram_user.id).with_for_update().first()

        total_price = context.user_data.get("total_price")
        if total_price is None:
            await query.edit_message_text("❌ لا يمكن تحديد السعر. يرجى المحاولة لاحقاً.")
            return ConversationHandler.END

        if user.balance < total_price:
            await query.edit_message_text(
                f"❌ رصيدك غير كافٍ. السعر: {total_price} ل.س، رصيدك: {user.balance} ل.س. يرجى شحن الرصيد أولاً."
            )
            return ConversationHandler.END

        user.balance -= total_price
        product_id = context.user_data["product_id"]
        product = db.query(Product).filter_by(id=product_id).first()

        order = Order(
            user_id=user.id,
            product_id=product_id,
            total_price_syp=total_price,
            status="pending",
            field_answers=json.dumps(context.user_data["answers"], ensure_ascii=False)
        )
        db.add(order)
        db.commit()
        db.refresh(order)

        await notify_admins(
            context.bot,
            f"📦 طلب جديد #{order.id}\n"
            f"المستخدم: {user.first_name or ''} {user.last_name or ''} (@{user.username or ''})\n"
            f"المنتج: {context.user_data['product_name']}\n"
            f"السعر: {total_price} ل.س\n"
            f"التفاصيل: {json.dumps(context.user_data['answers'], ensure_ascii=False)}"
        )

        # منطق الشراء الآلي
        if (settings.MHD_API_ENABLED and settings.MHD_API_KEY and
                product and product.is_auto):
            asyncio.create_task(
                process_auto_purchase(order.id, user.id, context.bot, context)
            )
            final_text = (
                f"✅ *تم استلام طلبك #{order.id} بنجاح!*\n\n"
                f"المنتج: {escape_markdown(context.user_data['product_name'])}\n"
                f"تم خصم {total_price} ل.س من رصيدك.\n"
                f"🔄 *جاري تنفيذ الطلب آلياً...* ستتلقى إشعاراً فور اكتماله."
            )
        else:
            final_text = (
                f"✅ *تم استلام طلبك #{order.id} بنجاح!*\n\n"
                f"المنتج: {escape_markdown(context.user_data['product_name'])}\n"
                f"تم خصم {total_price} ل.س من رصيدك.\n"
                f"سيتم مراجعة الطلب والتواصل معك قريباً."
            )

        await query.edit_message_text(final_text, parse_mode="Markdown")
        return ConversationHandler.END

    except Exception:
        logger.error("Exception in confirm_order:", exc_info=True)
        await query.edit_message_text("❌ حدث خطأ أثناء معالجة الطلب. يرجى المحاولة لاحقاً.")
        return ConversationHandler.END
    finally:
        db.close()


async def invalid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ يرجى إرسال الرد المطلوب فقط (نص أو صورة). الرجاء المحاولة مرة أخرى."
    )
    return await ask_next_field(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم إلغاء العملية. نأمل خدمتك في وقت لاحق.")
    return ConversationHandler.END

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
from deposit import start_deposit
async def exit_to_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("💰 جاري الانتقال إلى الشحن...")
    # استدعاء أمر /charge مباشرة (كما يفعل زر شحن الرصيد)
    return await start_deposit(update, context)

def order_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start_order, pattern="^product_")],
        states={
            SELECTING_FIELD: [
                CallbackQueryHandler(receive_answer, pattern="^(answer_|cancel_order|answer_back)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer),
                MessageHandler(filters.PHOTO, receive_answer),
                MessageHandler(~filters.COMMAND, invalid_input),
            ],
            CONFIRM_ORDER: [
                CallbackQueryHandler(confirm_order, pattern="^(confirm_order|edit_order|cancel_order|recharge_balance)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex('^🛒 الأقسام$'), exit_to_categories),
            CommandHandler("cancel", cancel),
            CommandHandler("start", exit_to_main_menu),
            CommandHandler("profile", exit_to_profile),
            CommandHandler("charge", exit_to_deposit),
            MessageHandler(filters.Regex('^🛒 الأقسام$'), exit_to_categories),  # لديك أساساً exit_to_categories
            MessageHandler(filters.Regex('^💰 شحن الرصيد$'), exit_to_deposit),
            MessageHandler(filters.Regex('^👤 حسابي$'), exit_to_profile),
        ],
    )


# -------------------- استرداد مهام الاستطلاع --------------------
async def recover_polling_jobs(application):
    db = SessionLocal()
    try:
        processing_orders = db.query(Order).filter(
            Order.status == "processing",
            Order.external_order_uuid.isnot(None)
        ).all()
        for order in processing_orders:
            started_at = datetime.utcnow()
            application.job_queue.run_once(
                poll_order_status,
                when=5,
                data={"order_id": order.id, "attempt": 1, "started_at": started_at.isoformat()},
                name=f"poll_order_{order.id}"
            )
            logger.info(f"Recovered polling for order {order.id}")
    except Exception as e:
        logger.error(f"Error recovering polling jobs: {e}", exc_info=True)
    finally:
        db.close()