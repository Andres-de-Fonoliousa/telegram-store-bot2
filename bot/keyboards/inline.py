# bot/keyboards/inline.py

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from core.database import SessionLocal
from core.models import Category

def main_menu_keyboard():
    """
    Fetches active categories from the database and builds the main menu.
    Callback data format: "category_{id}"
    """
    db = SessionLocal()
    try:
        categories = db.query(Category).filter_by(is_active=True).all()
        
        buttons = []
        for cat in categories:
            buttons.append([
                InlineKeyboardButton(
                    cat.name_ar, 
                    callback_data=f"category_{cat.id}"
                )
            ])
        
        return InlineKeyboardMarkup(buttons)
    finally:
        db.close()

def products_keyboard(products):
    """
    Creates a list of buttons for products.
    Each button shows the product's Arabic name.
    The callback_data will be: "product_{id}"
    """
    buttons = []
    for product in products:
        buttons.append([
            InlineKeyboardButton(
                product.name_ar, 
                callback_data=f"product_{product.id}"
            )
        ])
    
    # ✅ APPEND the back button, don't overwrite
    buttons.append([
        InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_to_main")
    ])
    
    return InlineKeyboardMarkup(buttons)

def back_to_main_keyboard():
    """A simple keyboard with just a back button."""
    buttons = [[InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_to_main")]]
    return InlineKeyboardMarkup(buttons)