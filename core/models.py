
# core/models.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from core.database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, index=True)
    username = Column(String, nullable=True)
    first_name = Column(String)
    last_name = Column(String, nullable=True)
    balance = Column(Integer, default=0)  # Store in SYP
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    discount_percent = Column(Integer, default=0)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name_ar = Column(String, nullable=False)
    name_en = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"))
    name_ar = Column(String, nullable=False)
    description_ar = Column(Text, nullable=True)
    base_price_syp = Column(Integer, default=0)  # Base price, can be overridden by admin later
    fields = Column(Text, nullable=True)         # JSON array of input field names
    validation = Column(Text, nullable=True)     # JSON object of validation rules
    image_path = Column(String, nullable=True)   # Relative path to image file
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    # --- الحقول الجديدة للشراء الآلي ---
    is_auto = Column(Boolean, default=False)  # هل المنتج يُشترى آلياً؟
    external_product_id = Column(Integer, nullable=True)
    markup_percentage = Column(Integer, default=0)  # نسبة الربح المضافة (مئوية)
    sync_enabled = Column(Boolean, default=False)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, default=1)
    total_price_syp = Column(Integer)
    status = Column(String, default="pending_payment")  # pending_payment, payment_sent, completed, cancelled
    screenshot_path = Column(String, nullable=True)
    code_delivered = Column(Text, nullable=True)
    # Store the answers to dynamic fields as JSON (e.g., {"player_id": "123456", "uc_amount": "60"})
    field_answers = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    external_order_uuid = Column(String(100), nullable=True)   # order_uuid من MHD
    auto_purchase_attempted = Column(Boolean, default=False)   # هل حاولنا الشراء الآلي

# Table for storing product prices for each option/quantity

# Table for storing product prices for each option/quantity
class ProductsPrice(Base):
    __tablename__ = "products_prices"
    __table_args__ = (UniqueConstraint('product_id', 'option_value', name='_product_option_uc'),)
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    option_value = Column(String, nullable=False)  # e.g., '60', '325', 'أسبوعي', etc.
    price_syp = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    external_product_id = Column(Integer, nullable=True) # معرف المنتج لدى MHD
    provider_cost = Column(Integer, nullable=True)       # التكلفة بالعملة الأصلية (مثلاً سنتات أو دولارات)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)  # آخر مزامنة

# Table for deposit/charging orders
class DepositOrder(Base):
    __tablename__ = "deposit_orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Integer, nullable=False)  # Amount to deposit (SYP)
    screenshot_path = Column(String, nullable=True)  # Path or Telegram file_id
    status = Column(String, default="pending_payment")  # pending_payment, completed, rejected
    admin_id = Column(Integer, nullable=True)  # Admin who approved
    admin_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class AdminLog(Base):
    __tablename__ = "admin_logs"
    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, nullable=False)  # Telegram ID of admin
    action = Column(String, nullable=False)  # e.g., 'update_price', 'approve_deposit'
    target_type = Column(String, nullable=False)  # 'price', 'order', 'deposit'
    target_id = Column(Integer, nullable=False)
    details = Column(Text, nullable=True)  # JSON string with before/after
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    id = Column(Integer, primary_key=True)
    rate = Column(Integer, nullable=False)  # SYP per 1 USD
    effective_date = Column(DateTime(timezone=True), server_default=func.now())
    created_by_admin_id = Column(Integer, nullable=False)
