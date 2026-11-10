# PROGRESS

Leer este archivo primero en cada sesión nueva. Última actualización: 2026-11-10.

## Estado en una línea
Sistema completo en **paper**, probado de punta a punta con Kraken y Jev reales. Ninguna estrategia supera
todavía a mantener BTC. Live bloqueado a la espera de tu aprobación.

## Qué está hecho

### Implementado y probado (75 pruebas automáticas, `python -m pytest -q`)
| Parte | Rutas |
|---|---|
| Motor de estado causal y chequeos de salud de datos | `src/reflexbot/state_engine.py` |
| Capa de riesgo dura y kill switch persistente | `src/reflexbot/risk.py`, `config/risk.yaml` |
| Compuerta, política versionada y tamaño por Kelly | `src/reflexbot/gate.py` |
| REFLEX: interfaz, latencia y plazo | `src/reflexbot/reflex/base.py` |
| Esquema tipado y compilación por finalista | `src/reflexbot/reflex/schema.py`, `config/schemas/` |
| Broker de paper con comisiones, slippage, liquidez y fallos | `src/reflexbot/exchange/paper.py` |
| Backtest walk-forward contra BTC y guarda de fecha de corte | `src/reflexbot/backtest.py` |
| Calibración (Brier, fiabilidad, calibrador) | `src/reflexbot/calibration.py` |
| Diario, políticas con promoción humana y rollback | `src/reflexbot/journal.py`, `src/reflexbot/policy_store.py` |
| Bucle nocturno | `src/reflexbot/nightly.py` |
| Runner 24/7, alertas, compuerta de live, CLI | `src/reflexbot/runner.py`, `alerts.py`, `live_gate.py`, `cli.py` |

### Ejecutado contra servicios reales
| Qué | Resultado |
|---|---|
| Datos de Kraken por ccxt (velas, libro, hora) | Funciona |
| Jev (`api.typesafe.ai`) | 3 143 decisiones, 0 fallos, p50 191 ms |
| Ciclo de paper completo (`run --once`) | 3 decisiones, 0 órdenes (Jev bajista y con poca confianza) |
| Backtests walk-forward de reglas (4 h y diario) | Ver `docs/REVIEW.md` |
| Replay de Jev posterior a su corte (1 h, 7 símbolos) | Ver `docs/REVIEW.md` |
| Recolector de investigación | `data/research/snapshot_2026-11-10T1705Z.json` |
| Revisión nocturna sobre el diario real | Corre; sin propuesta (faltan decisiones resueltas) |

### Simulado (no es real)
- Todas las órdenes: broker de paper. No se envió ninguna orden a Kraken.
- Slippage en backtest: modelo estimado. En paper en vivo el fill recorre el libro real.
- `toxic_flow` en backtest: nulo, porque no hay libro histórico.

### Escrito pero no ejecutado contra el servicio real
- Adaptador de Claude Haiku 5.5: no hay clave de Anthropic en el entorno. Probado con un cliente falso.
- Telegram: no hay token. Probado con una sesión falsa.
- `docker compose up`: este entorno no tiene daemon de Docker. El archivo se validó con `docker compose config`.
- Adaptador `KrakenLive`: bloqueado por la compuerta; nunca se construyó.

### Bloqueado
| Qué | Falta |
|---|---|
| Runtime 24/7 | Un servidor con Docker. Esta sesión es el taller, no el runtime, y su contenedor se recicla. |
| Órdenes reales | Tu aprobación explícita, clave de Kraken solo de trading y conectar `KrakenLive` al runner. |
| Alertas a Telegram | Token del bot y chat id en `.env`. |
| Universo más amplio (AAVE, UNI, HYPE…) | Que aceptes USD como moneda de cotización; Kraken casi no tiene pares USDT. |

## Qué sigue
1. Desplegar el paper en un servidor (`docs/RUNBOOK.md`) y dejarlo 30 días.
2. Decidir qué hacer con el hallazgo principal: con los umbrales de partida, Jev nunca dispara una entrada.
   Opciones: cambiar la pregunta de dirección a sí/no (dos opciones), o que el REFLEX deje de predecir dirección.
3. Rehacer el replay de Jev cuando haya 60 días o más posteriores a su corte y más de un régimen.
4. Añadir funding y contexto entre activos al snapshot y medir si mejora el Brier (fuera de muestra).
5. Verificar concentración de tenedores y calendarios de desbloqueo con fuentes primarias.

## Registro de decisiones

| Fecha | Decisión | Razón |
|---|---|---|
| 2026-11-10 | Kraken spot, 10 000 USDT de paper, sin monedas prohibidas, REFLEX con Jev | Respuestas del dueño |
| 2026-11-10 | Python con ccxt, pydantic, SQLite; un solo paquete `reflexbot` con CLI | Sin scripts sueltos; ccxt lo pide el encargo |
| 2026-11-10 | Añadí al entorno los hosts de Kraken, CoinGecko, DefiLlama y la documentación de TypeSafe | El proxy los bloqueaba; sin ellos no había datos reales ni referencia de la API |
| 2026-11-10 | La API de Jev se implementó leyendo `docs.typesafe.ai`, no de memoria | Regla: no inventar una API |
| 2026-11-10 | `direction_confidence` = probabilidad de la dirección elegida; la `confidence` de Jev se guarda aparte | La documentación de Jev dice que su `confidence` mide concentración, no acierto |
| 2026-11-10 | `risk_state` efectivo = el peor entre el del modelo y el del código | Ningún límite puede depender de un modelo |
| 2026-11-10 | Corte de Jev = fecha de lanzamiento (2026-10-22) | TypeSafe no publica el corte de entrenamiento; es una cota segura |
| 2026-11-10 | Sin testnet: paper sobre datos reales y `validate=true` antes de live | Kraken no tiene testnet de spot |
| 2026-11-10 | El tope de 2x queda en 1x efectivo | En spot no se puede pedir prestado |
| 2026-11-10 | Universo = BTC más 6 finalistas con par USDT | El dueño eligió USDT; Kraken tiene solo 44 pares USDT |
| 2026-11-10 | El titular de un backtest sin operaciones dice "sin operaciones", no "supera" | Quedarse fuera en un mercado bajista no es ventaja |
| 2026-11-10 | La pausa por baja confianza avisa al entrar y al salir del estado, no en cada vela | Con Jev se activaba en el 96 % de las velas: sería ruido |
| 2026-11-10 | Corregido el chequeo de desfase de reloj | Contaba la demora de los feeds como desfase (fallo real en la primera ejecución) |
| 2026-11-10 | No cambié los umbrales de partida pese a que Jev nunca los alcanza | Son del dueño, y ajustarlos sobre la misma muestra sería sobreajuste |
| 2026-11-10 | No usé subagentes | El trabajo no tenía partes paralelas que justificaran el costo |
| 2026-11-10 | Las pruebas se escribieron junto al código, no estrictamente antes | Se anota para no afirmar un "test-first" que no fue literal |
