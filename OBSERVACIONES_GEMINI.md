# Observaciones Profundas y Problemas Potenciales en AInvestor

A continuación detallo un análisis profundo de la arquitectura y el código del proyecto `AInvestor`, enfocándome en problemas subyacentes, race conditions, edge cases y arquitectura que podrían causar fallos críticos, especialmente en un entorno de producción o `live trading`.

## 1. Concurrencia en SQLite y Excepción "Database is locked"
**Archivos Afectados:** `main.py`, `models.py`, `dashboard/app.py`
**Problema:**
APScheduler (`BackgroundScheduler`) ejecuta sus tareas (p. ej., `task_check_stop_losses`, `task_agent_decision`) en **múltiples hilos (threads)**. Cada vez que una tarea necesita la base de datos, llama a `get_session()` guardando trades, snapshots o señales, y luego hace `.commit()`. 
SQLite bloquea (lock) la base de datos entera durante operaciones de escritura. Si el iterador de FastAPI (`dashboard/app.py`) solicita `/api/trades` exactamente en el mismo milisegundo en que el bot hace un `self.portfolio.save_snapshot()`, o si dos tareas del scheduler quieren escribir al mismo tiempo, el ORM de SQLAlchemy lanzará un `sqlite3.OperationalError: database is locked`.
**Solución recomendada:** 
- Al crear el engine en `models.py`, se debe configurar el timeout y el check del hilo: `create_engine(settings.db_url, connect_args={"check_same_thread": False, "timeout": 15})`.
- Idealmente, activar el modo WAL de SQLite para permitir lectores concurrentes mientras alguien escribe.

## 2. Race Conditions en WebSockets y Estado del Dashboard
**Archivos Afectados:** `dashboard/app.py`, `main.py`
**Problema:**
En `app.py`, `ws_manager.broadcast()` itera sobre la lista `self.active` usando un simple bucle `for ws in self.active:`. Como Uvicorn/FastAPI maneja conexiones WebSocket asíncronamente en un Event Loop, si un usuario se conecta o desconecta exactamente durante el ciclo del bucle for (modificando la lista con `.append()` o `.remove()`), Python lanzará un `RuntimeError: dictionary/list changed size during iteration` y tumbará el proceso de broadcast.
De igual forma, el diccionario global `_state` es modificado desde hilos paralelos por `update_dashboard_state()` en `main.py`. Múltiples mutaciones de diccionario desde distintos threads hacia FastAPI pueden inducir latencia o errores de estado.
**Solución recomendada:**
- El `broadcast` debería iterar sobre una copia de la lista: `for ws in self.active.copy():`
- Usar un `asyncio.Lock` para manejar la adición y eliminación de clientes, o pasar los mensajes del bot a FastAPI a través de un `asyncio.Queue`.

## 3. Manejo Crítico de Órdenes Limit en Live Trading
**Archivos Afectados:** `order_manager.py`, `portfolio.py`
**Problema:**
Recientemente se añadió la instrucción `order_type="limit"`. La API de Binance vía CCXT, al crear una orden *Limit*, simplemente la coloca en el *Order Book*. CCXT suele devolver el objeto de la orden con el campo `filled=0.0`, `status='open'`.
Posteriormente, `order_manager.py` extrae `filled_amount = order.get("filled", amount)` (pero cuidado, `order.get("filled")` puede devolver `0`, lo que causa que guarde 0), e inyecta esa cantidad vacía en `portfolio.open_position()`.
El portfolio asume que la orden fue "comprada", descuenta el dinero (basado en un `cost` que no se ha ejecutado) y abre una posición ficticia en el dashboard aunque no tienes las monedas aún. Nunca se chequea posteriormente si la limit order se *llenó*, lo que desincroniza totalmente el balance del `live trading`.
**Solución recomendada:**
- Para que la logica actual del portfolio funcione sincrónicamente, el bot en "Live" debería usar exclusívamente órdenes Market o enviar la orden Limit y luego iterar revisando `ccxt.fetch_order(id)` activamente hasta que pase de `open` a `closed`, o usar una arquitectura asíncrona (WebSocket de Binance User Data Stream) para escuchar fills.

## 4. Desajuste Entre el Timeframe de las Velas y el Intervalo del Análisis Técnico
**Archivos Afectados:** `main.py`, `config.py`
**Problema:**
En `config.py`, `ohlcv_timeframe` está seteado en `1h` (Velas de 1 hora) por defecto. Sin embargo, `task_market_data` se ajusta cada 5 minutos, y `task_technical_analysis` se evalúa cada 15 minutos. 
Por lo tanto, durante 59 minutos de cada hora, la última vela en el DataFrame no está "cerrada" (unclosed candle). Indicadores sensibles como MACD, EMA Crossovers e incluso RSI darán brincos erróneos porque calculan un cierre falso con el precio fluctuante actual, y el Agente generará señales base a *ruido intranquila*. En trading algorítmico tradicional, se evalúa la señal únicamente en el momento que cierra la vela.
**Solución recomendada:**
- Si quieres trading reactivo de 15 minutos, el `ohlcv_timeframe` debería ser `15m` y el agente correr cada que cierre la vela de 15m. Si quieres mantener la visión macroética de `1h`, el análisis TA solo debería generar trigger a la hora en punto, utilizando el cierre estricto de la vela.

## 5. Falta de Tratamiento Exponencial en Rate Limits
**Archivos Afectados:** `data/market_data.py`
**Problema:**
Binance aplica límites de peso de Request de manera estricta. El bot actual utiliza un `time.sleep(0.2)` rudimentario y CCXT por defecto sin backoff estructurado. Si se produce un pico de consultas o un `HTTP 429 Too Many Requests`, la API CCXT levantará un `RateLimitExceeded`. Al no haber lógica de `retry` con un backoff exponencial, esta excepción se filtrará hacia `task_fetch_market_data()`, abortando esa iteración, perdiendo el ciclo del agente IA entero y dejando a ciegas al *Risk Manager y Stop-Loss* hasta la próxima vuelta. 
**Solución recomendada:**
- Decorar las peticiones a CCXT con un wrapper que atrape `RateLimitExceeded`, haga pause (`time.sleep()`), y reintente para prevenir la caída en cascada de los métodos del scheduler. Usar flag `enableRateLimit=True` en la instanciación de CCXT.

## 6. Problema Subyacente del Análisis de Sentimiento (VADER NLP Model)
**Archivos Afectados:** `data/sentiment.py`
**Problema:**
La librería `vaderSentiment` está diseñada fundamentalmente para medir el sentimiento de *redes sociales tradicionales* y *reviews en foros*, donde hay puntuación efusiva como ("I love it!!!"). Es **pésimo** interpretando jergas financieras. 
Por ejemplo: un titular *"Inflation and Fed rates unexpectedly drop"* generará un score negativo en VADER por la palabra "drop" (caer), mientras que en realidad es una noticia extremadamente alcista (Bullish) para Crypto. VADER arroja demasiado falso negativo financiero.
**Solución recomendada:**
- Las noticias en crudo deberían ser inyectadas directamente dentro del prompt hacia `<Gemini>`, o en su defecto usar un modelo tipo `FinBERT` pre-entrenado para el mercado (si se desea procesar localmente). La integración actual, a pesar de bajarle el peso a 40%, inyectará ruido bajista injustificado en el mercado positivo.

## 7. Cierres Brutales del BackgroundScheduler
**Archivos Afectados:** `main.py`
**Problema:**
FastAPI (corriendo dentro de `uvicorn.run()`) bloquea el thread principal. Cuando presionas `CTRL+C`, uvicorn captura el cierre. En el `finally`, `scheduler.shutdown(wait=False)` es llamado. 
Al usar `wait=False`, si en ese momento una tarea interna (por ej. `task_daily_report`) de Python está grabando en el file JSON interno, o a mitad de un trade API real dentro de CCXT, el proceso se truncará instantáneamente, corrompiendo la DB local o dejando un *orphaned order* que se quedó enviado en Binance pero nunca guardado en SQLite local.
**Solución recomendada:**
- Cambiar a `scheduler.shutdown(wait=True)` para asegurar limpieza, y capturar el System Exit global de Python (Signal Handling SIGINT/SIGTERM) coordinado correctamente con el event loop principal.
