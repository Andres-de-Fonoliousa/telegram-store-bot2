from telegram import ReplyKeyboardMarkup, KeyboardButton

def main_reply_keyboard():
    keyboard = [
        [KeyboardButton("الألعاب 🎮🔥")],
        [KeyboardButton("الرَّشق ⚡📱")],
        [KeyboardButton("شحن الرصيد 💎")],
        [KeyboardButton("حسابي 👤")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)