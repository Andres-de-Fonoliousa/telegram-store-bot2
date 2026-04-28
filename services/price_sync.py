import logging
from datetime import datetime
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from telegram.ext import CallbackContext

from core.database import SessionLocal
from core.models import Product, ProductsPrice, ExchangeRate
from services.mhd_api import MHDStoreAPI
from config import settings

logger = logging.getLogger(__name__)


def get_latest_exchange_rate(db: Session) -> int:
    """جلب أحدث سعر صرف من قاعدة البيانات."""
    rate_obj = db.query(ExchangeRate).order_by(ExchangeRate.effective_date.desc()).first()
    if rate_obj:
        return rate_obj.rate
    # قيمة افتراضية (يمكن ضبطها من settings لاحقاً)
    return 10000

async def check_mhd_balance_and_alert(context: CallbackContext):
    """التحقق من رصيد MHD وإرسال تحذير للمشرفين إذا كان منخفضاً."""
    if not settings.MHD_API_ENABLED or not settings.MHD_BALANCE_CHECK_ENABLED:
        return

    try:
        api = MHDStoreAPI(settings.MHD_API_KEY, settings.MHD_API_BASE_URL)
        balance = api.get_balance_usd()
        if balance is None:
            return

        threshold = settings.MHD_LOW_BALANCE_THRESHOLD
        if balance < threshold:
            # إرسال تحذير للمشرفين
            message = (
                f"⚠️ *تحذير: رصيد MHD منخفض*\n"
                f"الرصيد الحالي: `${balance:.2f}`\n"
                f"الحد الأدنى المحدد: `${threshold:.2f}`\n"
                f"يرجى شحن الرصيد لتجنب فشل الطلبات الآلية."
            )
            for admin_id in settings.ADMIN_IDS:
                try:
                    await context.bot.send_message(chat_id=admin_id, text=message, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Failed to send low balance alert to admin {admin_id}: {e}")
    except Exception as e:
        logger.error(f"Error in MHD balance check: {e}")

def sync_prices_from_mhd(context=None) -> Dict[str, Any]:
    """مزامنة الأسعار مع احترام إعداد sync_enabled لكل منتج."""
    if not settings.MHD_API_ENABLED:
        logger.info("MHD API disabled, skipping price sync")
        return {"status": "disabled"}

    db = SessionLocal()
    try:
        api = MHDStoreAPI(settings.MHD_API_KEY, settings.MHD_API_BASE_URL)
        products_data = api.get_all_products()

        exchange_rate = get_latest_exchange_rate(db)
        updated_count = 0
        skipped_count = 0

        mhd_products_map = {p["id"]: p for p in products_data if p.get("id")}

        # --- 1. المنتجات التقليدية (خيارات ثابتة) ---
        price_records = db.query(ProductsPrice).filter(
            ProductsPrice.external_product_id.isnot(None)
        ).all()

        for price_record in price_records:
            # التحقق من sync_enabled للمنتج المرتبط
            product = db.query(Product).filter_by(id=price_record.product_id).first()
            if not product or not product.sync_enabled:
                skipped_count += 1
                continue

            mhd_prod = mhd_products_map.get(price_record.external_product_id)
            if not mhd_prod:
                continue

            provider_price_usd = mhd_prod.get("price", 0.0)
            provider_cost_syp = int(provider_price_usd * exchange_rate)
            markup = product.markup_percentage or 0
            final_price = int(provider_cost_syp * (1 + markup / 100))

            price_record.provider_cost = provider_cost_syp
            price_record.price_syp = final_price
            price_record.last_synced_at = datetime.utcnow()
            updated_count += 1

        # --- 2. المنتجات ذات الكمية المفتوحة ---
        open_quantity_products = db.query(Product).filter(
            Product.is_auto == True,
            Product.external_product_id.isnot(None)
        ).all()

        for product in open_quantity_products:
            if not product.sync_enabled:
                skipped_count += 1
                continue

            mhd_prod = mhd_products_map.get(product.external_product_id)
            if not mhd_prod:
                continue

            unit_record = db.query(ProductsPrice).filter_by(
                product_id=product.id,
                option_value='unit'
            ).first()
            if not unit_record:
                continue

            provider_price_usd = mhd_prod.get("price", 0.0)
            provider_cost_syp = int(provider_price_usd * exchange_rate)
            markup = product.markup_percentage or 0
            final_price = int(provider_cost_syp * (1 + markup / 100))

            unit_record.provider_cost = provider_cost_syp
            unit_record.price_syp = final_price
            unit_record.last_synced_at = datetime.utcnow()
            updated_count += 1

        db.commit()
        logger.info(f"Price sync completed: {updated_count} updated, {skipped_count} skipped")
        return {"status": "success", "updated": updated_count, "skipped": skipped_count}

    except Exception as e:
        logger.error(f"Price sync failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
    finally:
        db.close()