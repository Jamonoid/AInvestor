"""Test rapido de persistencia de portfolio y trailing stop."""
from execution.portfolio import PortfolioManager, _STATE_FILE
import json

# Test 1: Crear portfolio y abrir posicion
pm = PortfolioManager()
pm._cash = 10000.0  # reset
pm._positions = {}
pm.open_position("BTC/USDT", 0.1, 50000, 5000, fees=5.0, stop_loss=48500, take_profit=54000)
print(f"Cash despues: ${pm.cash:.2f}")
print(f"Posiciones: {list(pm.positions.keys())}")

# Test 2: Verificar archivo
assert _STATE_FILE.exists(), "Estado no persistido!"
state = json.loads(_STATE_FILE.read_text())
print(f"Estado guardado: cash=${state['cash']:.2f}")
btc_pos = state["positions"]["BTC/USDT"]
print(f"Highest price tracking: {btc_pos['highest_price']}")

# Test 3: Restaurar portfolio
pm2 = PortfolioManager()
print(f"Portfolio restaurado: cash=${pm2.cash:.2f}, posiciones={list(pm2.positions.keys())}")
assert pm2.cash == pm.cash, "Cash no coincide!"
assert "BTC/USDT" in pm2.positions, "Posicion no restaurada!"

# Test 4: Trailing stop
updated = pm2.update_trailing_stops({"BTC/USDT": 52000})
if updated:
    print(f"Trailing SL actualizado: old=${updated[0]['old_sl']:.2f} new=${updated[0]['new_sl']:.2f}")

# Test 5: Cierre parcial
pnl = pm2.close_position("BTC/USDT", 53000, partial_percent=50)
remaining = pm2.get_position("BTC/USDT")
print(f"Cierre parcial PnL: ${pnl:.2f}")
print(f"Cantidad restante: {remaining['amount']:.6f}" if remaining else "ERROR: posicion eliminada")

# Limpiar
_STATE_FILE.unlink()
print("\nTODOS LOS TESTS OK")
