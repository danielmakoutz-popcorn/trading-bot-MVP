def position_size(equity: float, risk_fraction: float, price: float) -> float:
    if price <= 0:
        return 0.0
    # Risk fraction of equity used to size a simple market order qty
    dollars = max(0.0, equity * float(risk_fraction))
    qty = dollars / price
    return float(max(0.0, round(qty, 4)))
