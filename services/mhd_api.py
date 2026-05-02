import logging
import uuid
import requests
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class MHDAPIError(Exception):
    """استثناء مخصص لأخطاء MHD API."""
    pass


class MHDStoreAPI:
    def __init__(self, api_key: str, base_url: str = "https://api.shams4store.com/client/api"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "api-token": self.api_key,
            "Accept": "application/json"
        }

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        url = f"{self.base_url}{endpoint}"
        print(f"DEBUG REQUEST: URL={url}, params={params}, headers={self.headers}")
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=self.headers, params=params, timeout=15)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            # إذا كان الكود ليس 2xx، ارمِ خطأ مع تفاصيل
            if not response.ok:
                logger.error(f"MHD API HTTP error: {response.status_code} for {url} - {response.text[:200]}")
                raise MHDAPIError(f"HTTP error {response.status_code}: {response.text[:100]}")

            result = response.json()

            # التحقق من حالة الـ API (قد تكون "OK" أو True للنجاح، وأي شيء آخر خطأ)
            status = result.get("status")
            if status not in (True, "OK", "true", "accept"):
                error_msg = result.get("message", "Unknown API error")
                logger.error(f"MHD API error: {error_msg}")
                raise MHDAPIError(f"API error: {error_msg}")

            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"MHD API request failed: {e}")
            raise MHDAPIError(f"Network error: {e}") from e
        except ValueError as e:
            logger.error(f"Invalid JSON response: {e}")
            raise MHDAPIError(f"Invalid response format: {e}") from e

    def get_balance_usd(self) -> Optional[float]:
        """جلب رصيد المحفظة بالدولار."""
        try:
            profile = self.get_profile()
            balance = profile.get("balance")
            if balance is not None:
                return float(balance)
        except Exception as e:
            logger.error(f"Failed to get MHD balance: {e}")
        return None

    def create_order(
            self,
            product_id: int,
            player_id: str,
            quantity: int = 1,
            idempotency_key: Optional[str] = None,
            extra_params: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        إنشاء طلب شراء وإرجاع order_uuid (مفتاح إمبوتنسي) لاستخدامه في الاستعلام عن الحالة.
        """
        if idempotency_key is None:
            idempotency_key = str(uuid.uuid4())

        endpoint = f"/newOrder/{product_id}/params"
        params = {
            "qty": quantity,
            "playerId": player_id,
            "order_uuid": idempotency_key
        }
        if extra_params:
            params.update(extra_params)
            # ضمان عدم استبدال order_uuid عن طريق الخطأ
            params["order_uuid"] = idempotency_key
        result = self._request("GET", endpoint, params=params)
        print(result)
        data = result.get("data", {})
        order_id = data.get("order_id")
        if not order_id:
            raise MHDAPIError("Response missing order_id")
        # نعيد الـ UUID ليتم استخدامه في get_order_status
        return idempotency_key

    def get_order_status(self, order_identifier: str) -> Dict[str, Any]:
        """
        استعلام عن حالة الطلب باستخدام order_uuid.
        يُرجع قاموساً يحتوي على:
        - status: "completed" (مكتمل), "failed" (فشل), "processing" (قيد الانتظار)
        - delivered_data: البيانات المسلمة (قد تكون None)
        - price, quantity, product_name, raw
        """
        params = {
            "orders": order_identifier,
            "uuid": 1
        }

        result = self._request("GET", "/check", params=params)
        data_list = result.get("data", [])
        if not data_list:
            raise MHDAPIError("No order data found")

        order_data = data_list[0]
        # تحويل الحالة إلى ما يتوقعه باقي الكود
        api_status = order_data.get("status")  # "accept", "reject", "wait"
        if api_status == "accept":
            normalized_status = "completed"
        elif api_status in ("reject", "fail"):
            normalized_status = "failed"
        elif api_status == "wait":
            normalized_status = "processing"
        else:
            normalized_status = "processing"  # افتراضي

        delivered = order_data.get("replay_api")
        # قد يكون replay_api قائمة أو None، نتعامل معه كنص إذا أمكن
        if isinstance(delivered, list):
            delivered = "\n".join(str(x) for x in delivered)

        return {
            "status": normalized_status,
            "delivered_data": delivered,
            "price": order_data.get("price"),
            "quantity": order_data.get("quantity"),
            "product_name": order_data.get("product_name"),
            "raw": order_data
        }

    def get_profile(self) -> Dict[str, Any]:
        """جلب معلومات الملف الشخصي (الرصيد)."""
        return self._request("GET", "/profile")

    def get_all_products(self) -> List[Dict[str, Any]]:
        """
        جلب قائمة جميع المنتجات من MHD API.
        """
        url = f"{self.base_url}/products"
        response = requests.get(url, headers=self.headers, timeout=15)
        # معالجة حالة خاصة إذا كانت الاستجابة بايتات (تعامل قديم)
        if isinstance(response, bytes):
            import json
            products_data = json.loads(response.decode('utf-8'))
            return products_data
        return response.json()

    def get_wallet_balance(self) -> Dict[str, Any]:
        """الاستعلام عن رصيد المحفظة."""
        profile = self.get_profile()
        # استخدام None كقيمة افتراضية ثم التحقق من المفاتيح لتجنب تجاهل الرصيد 0
        balance = profile.get("balance")
        if balance is None:
            balance = profile.get("wallet_balance")
        if balance is None:
            balance = profile.get("credit")
        currency = profile.get("currency") or "USD"
        return {
            "balance": balance,
            "currency": currency
        }