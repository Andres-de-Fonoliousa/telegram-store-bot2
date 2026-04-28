# core/bot_state.py
bot_active = True

def set_bot_active(state: bool):
    global bot_active
    bot_active = state

def is_bot_active():
    return bot_active