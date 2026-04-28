# bot/handlers/navigation.py
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

async def exit_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    from bot.handlers.user import show_main_menu
    await show_main_menu(update, context)
    return ConversationHandler.END

async def exit_to_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    from bot.handlers.user import show_profile
    await show_profile(update, context)
    return ConversationHandler.END

async def exit_to_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    from bot.handlers.user import show_categories
    await show_categories(update, context)
    return ConversationHandler.END

async def exit_to_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("💰 جاري الانتقال إلى الشحن...")
    from bot.conversations.deposit import start_deposit
    return await start_deposit(update, context)