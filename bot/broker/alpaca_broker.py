# bot/broker/alpaca_broker.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Tuple, Any

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce


@dataclass(frozen=True)
class PositionSnapshot:
    qty: float = 0.0
    avg_price: float = 0.0


class AlpacaBroker:
    """
    Alpaca broker adapter with stable semantics.

    Key rules:
    - "No position" is NOT an error. It means qty=0.
    - Position APIs never raise. They return (0,0) on any error.
    - Engine should treat these methods as the only source of truth for position state.
    """

    def __init__(
        self,
        db,
        log,
        api_key: str,
        api_secret: str,
        base_url: str = "https://paper-api.alpaca.markets",
        paper: bool = True,
    ):
        self.db = db
        self.log = log
        self.base_url = base_url

        # Alpaca v3 TradingClient: paper=True routes to paper trading automatically.
        self.client = TradingClient(api_key, api_secret, paper=paper)

        # Optional: last known quantities for quick UI/debug (NOT authoritative).
        self._last_qty_map: Dict[str, float] = {}

    # ----------------------------
    # helpers
    # ----------------------------
    @staticmethod
    def _is_no_position_error(exc: Exception) -> bool:
        """
        Alpaca SDK exceptions vary by version. We detect "no position" robustly.
        """
        msg = str(exc).lower()
        if "position does not exist" in msg:
            return True
        if "40410000" in msg:
            return True
        # Some SDKs include HTTP status text in message
        if "404" in msg and "position" in msg:
            return True
        return False

    def _safe_float(self, v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except Exception:
            return default

    # ----------------------------
    # account
    # ----------------------------
    @property
    def cash(self) -> float:
        try:
            acct = self.client.get_account()
            return self._safe_float(getattr(acct, "cash", 0.0))
        except Exception as e:
            self.log.warning(f"[BROKER] cash lookup failed: {e}")
            return 0.0

    @property
    def buying_power(self) -> float:
        try:
            acct = self.client.get_account()
            return self._safe_float(getattr(acct, "buying_power", 0.0))
        except Exception as e:
            self.log.warning(f"[BROKER] buying_power lookup failed: {e}")
            return 0.0

    @property
    def equity(self) -> float:
        try:
            acct = self.client.get_account()
            return self._safe_float(getattr(acct, "equity", 0.0))
        except Exception as e:
            self.log.warning(f"[BROKER] equity lookup failed: {e}")
            return 0.0

    # ----------------------------
    # positions (truth)
    # ----------------------------
    def list_positions(self):
        """
        Return list of Alpaca Position objects.
        Use only if you explicitly need the full list (force-flat, UI, etc.)
        """
        try:
            positions = self.client.get_all_positions()
            # keep optional debug map
            self._last_qty_map = {p.symbol.upper(): self._safe_float(p.qty) for p in positions}
            return positions
        except Exception as e:
            self.log.warning(f"[BROKER] list_positions failed: {e}")
            self._last_qty_map = {}
            return []

    def get_position_dict(self, symbol: str) -> Dict[str, float]:
        """
        Always returns a dict: {"qty": float, "avg_price": float}
        Never raises. Flat -> zeros.
        """
        sym = str(symbol).upper()
        try:
            p = self.client.get_open_position(sym)
            qty = self._safe_float(getattr(p, "qty", 0.0))
            avg = self._safe_float(getattr(p, "avg_entry_price", 0.0))
            # update optional debug cache
            self._last_qty_map[sym] = qty
            return {"qty": qty, "avg_price": avg}
        except Exception as e:
            if self._is_no_position_error(e):
                self._last_qty_map[sym] = 0.0
                return {"qty": 0.0, "avg_price": 0.0}

            # Real error: warn once and treat as flat (safe behavior)
            self.log.warning(f"[BROKER] get_position({sym}) failed; treating as flat: {e}")
            self._last_qty_map[sym] = 0.0
            return {"qty": 0.0, "avg_price": 0.0}

    def position_info(self, symbol: str) -> Tuple[float, float]:
        """
        Convenience: returns (qty, avg_price)
        """
        pos = self.get_position_dict(symbol)
        return float(pos["qty"]), float(pos["avg_price"])

    def position_qty(self, symbol: str) -> float:
        qty, _ = self.position_info(symbol)
        return qty

    def has_position(self, symbol: str, eps: float = 1e-6) -> bool:
        return self.position_qty(symbol) > eps

    def get_cached_qty(self, symbol: str) -> float:
        """
        Optional non-authoritative cache for UI/logging.
        Do NOT use for trading decisions.
        """
        return float(self._last_qty_map.get(str(symbol).upper(), 0.0))

    # ----------------------------
    # orders
    # ----------------------------
    def market_buy(
        self,
        ts,  # ignored; kept for engine compatibility
        symbol: str,
        qty: float,
        price: Optional[float] = None,  # ignored for MKT, kept for compatibility
        tag: str = "",
        tif: TimeInForce = TimeInForce.DAY,
    ):
        """
        Submit a market BUY order. Returns order id (or None).
        """
        sym = str(symbol).upper()
        q = float(qty)
        if q <= 0:
            self.log.info(f"[BROKER] BUY ignored non-positive qty: {sym} qty={q}")
            return None

        try:
            order = MarketOrderRequest(
                symbol=sym,
                qty=q,
                side=OrderSide.BUY,
                time_in_force=tif,
            )
            o = self.client.submit_order(order_data=order)
            oid = getattr(o, "id", None)
            self.log.info(f"[ORDER_SUBMIT] BUY {sym} qty={q} id={oid} tag={tag}")
            return oid
        except Exception as e:
            self.log.warning(f"[ORDER_ERR] BUY {sym} qty={q} failed: {e}")
            return None

    def market_sell(
        self,
        ts,  # ignored; kept for engine compatibility
        symbol: str,
        qty: float,
        price: Optional[float] = None,  # ignored for MKT, kept for compatibility
        tag: str = "",
        tif: TimeInForce = TimeInForce.DAY,
    ):
        """
        Submit a market SELL order. Returns order id (or None).
        """
        sym = str(symbol).upper()
        q = float(qty)
        if q <= 0:
            self.log.info(f"[BROKER] SELL ignored non-positive qty: {sym} qty={q}")
            return None

        try:
            order = MarketOrderRequest(
                symbol=sym,
                qty=q,
                side=OrderSide.SELL,
                time_in_force=tif,
            )
            o = self.client.submit_order(order_data=order)
            oid = getattr(o, "id", None)
            self.log.info(f"[ORDER_SUBMIT] SELL {sym} qty={q} id={oid} tag={tag}")
            return oid
        except Exception as e:
            self.log.warning(f"[ORDER_ERR] SELL {sym} qty={q} failed: {e}")
            return None

    def market_buy_to_cover(self, symbol: str, qty: float, tag: str = ""):
        """
        For shorts, covering is just a BUY. Kept for force-flat compatibility.
        """
        return self.market_buy(None, str(symbol).upper(), float(qty), tag=tag)
