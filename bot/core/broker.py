import json
import time
from typing import Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

class AlpacaBroker:
    """
    Minimal Alpaca broker that submits real MARKET orders to Alpaca (paper/live
    depending on base_url). Keeps local DB/log consistent with PaperBroker
    interface: market_buy, market_sell, get_position, equity.
    """

    def __init__(self, db, logger, api_key: str, api_secret: str, base_url: str = "https://paper-api.alpaca.markets"):
        self.db = db
        self.log = logger
        self.api_key = api_key
        self.api_secret = api_secret
        # Ensure no trailing slash
        self.base_url = base_url.rstrip("/")
        self._last_account_cache = None
        self._last_account_ts = 0.0

    # ---------- low-level HTTP helpers ----------
    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get(self, path: str) -> Dict:
        url = f"{self.base_url}{path}"
        req = Request(url, headers=self._headers(), method="GET")
        try:
            with urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"GET {path} failed: {e.code} {body}") from None
        except URLError as e:
            raise RuntimeError(f"GET {path} network error: {e}") from None

    def _post(self, path: str, payload: Dict) -> Dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"POST {path} failed: {e.code} {body}") from None
        except URLError as e:
            raise RuntimeError(f"POST {path} network error: {e}") from None

    # ---------- required interface ----------

    @property
    def cash(self) -> float:
        """Current cash from Alpaca account (cached ~2s)."""
        now = time.time()
        if self._last_account_cache and (now - self._last_account_ts) < 2.0:
            return float(self._last_account_cache.get("cash", 0.0))
        acct = self._get("/v2/account")
        self._last_account_cache = acct
        self._last_account_ts = now
        return float(acct.get("cash", 0.0))

    @property
    def equity(self) -> float:
        """Current equity from Alpaca account (cached ~2s)."""
        now = time.time()
        if self._last_account_cache and (now - self._last_account_ts) < 2.0:
            return float(self._last_account_cache.get("equity", 0.0))
        acct = self._get("/v2/account")
        self._last_account_cache = acct
        self._last_account_ts = now
        return float(acct.get("equity", 0.0))



    def get_position(self, symbol: str) -> Dict:
        """
        Return a dict similar to PaperBroker: {'qty': float, 'avg_price': float}
        If no position, return zeros.
        """
        sym = symbol.upper()
        try:
            pos = self._get(f"/v2/positions/{sym}")
        except RuntimeError as e:
            # 404 => no position
            if "404" in str(e):
                return {"qty": 0.0, "avg_price": 0.0}
            raise
        qty = float(pos.get("qty", 0.0))
        # Alpaca avg_entry_price may be str
        avg_price = float(pos.get("avg_entry_price", 0.0) or 0.0)
        return {"qty": qty, "avg_price": avg_price}

    def _submit_market_order(self, symbol: str, qty: float, side: str) -> Dict:
        """
        Submit a MARKET DAY order. qty must be positive.
        """
        if qty <= 0:
            return {"status": "ignored", "reason": "non_positive_qty"}
        payload = {
            "symbol": symbol.upper(),
            "qty": str(qty),            # Alpaca accepts string numbers
            "side": side,               # "buy" or "sell"
            "type": "market",
            "time_in_force": "day",
        }
        return self._post("/v2/orders", payload)

    def market_buy(self, ts, symbol: str, qty: float, price: float):
        """
        Place market buy, then mirror trade into local DB to keep stats consistent.
        """
        resp = self._submit_market_order(symbol, qty, "buy")
        self.log.info(f"Alpaca BUY submitted: {resp}")
        # local mirror (pnl = 0 for buys)
        self.db.insert_trade(ts, symbol, "buy", qty, price, pnl=0.0)

    def market_sell(self, ts, symbol: str, qty: float, price: float):
        """
        Place market sell, then mirror trade into local DB with realized PnL.
        We estimate realized PnL using current avg entry price from Alpaca.
        """
        old = self.get_position(symbol)
        avg_entry = float(old.get("avg_price", 0.0))
        resp = self._submit_market_order(symbol, qty, "sell")
        self.log.info(f"Alpaca SELL submitted: {resp}")
        realized_pnl = qty * (float(price) - avg_entry)
        self.db.insert_trade(ts, symbol, "sell", qty, price, pnl=realized_pnl)
