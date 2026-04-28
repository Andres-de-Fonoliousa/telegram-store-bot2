from telegram import ReplyKeyboardMarkup, KeyboardButton

def main_reply_keyboard():
    keyboard = [
        [KeyboardButton("🛒 الأقسام")],
        [KeyboardButton("💰 شحن الرصيد")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
