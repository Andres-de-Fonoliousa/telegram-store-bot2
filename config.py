# config.py
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./store.db")

    # Parse admin list (comma separated string -> list of ints)
    admin_str = os.getenv("ADMIN_USER_IDS", "")
    ADMIN_IDS = [int(uid.strip()) for uid in admin_str.split(",") if uid.strip()]

    # Paths
    MEDIA_PRODUCTS = "media/products"
    MEDIA_SCREENSHOTS = "media/screenshots"

    # قديمة - قد تبقى للتوافق أو تحذف لاحقاً
    # API_TOKEN = os.getenv("API_TOKEN")
    # API_BASE_URL = os.getenv("API_BASE_URL")
    # API_ACTIVE = os.getenv("API_ACTIVE")

    # MHD API Settings (الجديدة)
    MHD_API_KEY = os.getenv("MHD_API_KEY")
    MHD_API_BASE_URL = os.getenv("MHD_API_BASE_URL", "https://mhd-game.com/api/client/api")
    MHD_API_ENABLED = os.getenv("MHD_API_ENABLED", "false").lower() == "true"

    SYNC_ENABLED = os.getenv("SYNC_ENABLED", "true").lower() == "true"
    SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "60"))

    MHD_LOW_BALANCE_THRESHOLD = float(os.getenv("MHD_LOW_BALANCE_THRESHOLD", "5.0"))
    MHD_BALANCE_CHECK_ENABLED = os.getenv("MHD_BALANCE_CHECK_ENABLED", "true").lower() == "true"


settings = Settings()