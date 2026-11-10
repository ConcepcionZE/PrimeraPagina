# Arquitectura (fase 2)

## Flujo de una vela

```
Kraken (velas, libro, hora del servidor)
        │
        ▼
state_engine.health_issues ──► hay problemas ──► no hay órdenes, se registra y se alerta
        │ sano
        ▼
state_engine.build_snapshot  (estado numérico < 400 tokens, solo datos ya cerrados)
        │
        ▼
reflex.timed_decide  (Jev | Haiku 5.5 | reglas)  ──► error o fuera de plazo ──► se salta la vela
        │ ReflexDecision tipada
        ▼
calibration.Calibrator  (probabilidad cruda → calibrada)
        │
        ▼
gate.gate  (umbrales de la política)  ──► hold / exit / enter_long
        │ enter_long
        ▼
gate.position_size  (Kelly fraccional, tope 1/4 de Kelly)
        │
        ▼
risk.RiskEngine.check  (límites duros; recorta o rechaza)
        │ aprobado
        ▼
exchange.paper.PaperBroker  (fill contra el libro real, comisión, slippage)
        │
        ▼
journal (SQLite) ──► nightly (revisión, Brier, propuesta versionada)
```

## Módulos y rutas

| Ruta | Responsabilidad |
|---|---|
| `src/reflexbot/types.py` | Contratos tipados: `ReflexDecision`, `Candle`, `OrderBook`, `AccountView`, `OrderIntent`, `Fill` |
| `src/reflexbot/state_engine.py` | Snapshot causal, indicadores, chequeos de salud de datos, límite de tokens |
| `src/reflexbot/risk.py` | Límites duros, kill switch con cerrojo persistente, `risk_state` calculado en código |
| `src/reflexbot/gate.py` | Política versionada (umbrales), compuerta de entrada/salida, tamaño por Kelly |
| `src/reflexbot/reflex/base.py` | Interfaz del REFLEX, medición de latencia y plazo, fechas de corte por modelo |
| `src/reflexbot/reflex/schema.py` | Las preguntas tipadas y el esquema compilado por finalista |
| `src/reflexbot/reflex/jev.py` | Adaptador de Jev (`POST https://api.typesafe.ai/v1/systemone`) |
| `src/reflexbot/reflex/haiku.py` | Adaptador de Claude Haiku 5.5 (esfuerzo bajo, salida estructurada) |
| `src/reflexbot/reflex/rules.py` | REFLEX de reglas en código: línea base, mock y pruebas sobre datos antiguos |
| `src/reflexbot/exchange/paper.py` | Broker de paper: comisiones, slippage, liquidez, fallos, conciliación |
| `src/reflexbot/exchange/kraken.py` | Datos públicos de Kraken (ccxt) y adaptador live bloqueado |
| `src/reflexbot/live_gate.py` | La compuerta de live: tres condiciones, todas manuales |
| `src/reflexbot/calibration.py` | Brier, tabla de fiabilidad, calibrador monótono |
| `src/reflexbot/journal.py` | Diario SQLite de decisiones, fallos, fills y equity |
| `src/reflexbot/backtest.py` | Backtest por eventos y walk-forward contra mantener BTC |
| `src/reflexbot/policy_store.py` | Versiones de política, promoción solo humana, rollback |
| `src/reflexbot/nightly.py` | Bucle nocturno: propone, pasa compuertas, versiona; no promueve |
| `src/reflexbot/runner.py` | Bucle 24/7 de paper, stops, kill switch, conciliación, heartbeat |
| `src/reflexbot/alerts.py` | Alertas a Telegram (si hay token) y al log |
| `src/reflexbot/research/collect.py` | Recolector de datos de régimen con fecha y fuente |
| `src/reflexbot/cli.py` | Único punto de entrada: `python -m reflexbot <comando>` |
| `config/default.yaml` | Ajustes del runtime (sin secretos) |
| `config/risk.yaml` | Límites duros; solo los edita el dueño |
| `config/policies/` | Políticas versionadas y `registry.json` |
| `config/finalists.json`, `config/schemas/` | Finalistas y su esquema compilado |
| `Dockerfile`, `docker-compose.yml` | Runtime 24/7 |

## Decisiones de diseño y su razón

1. **El `risk_state` efectivo es el peor entre el del modelo y el del código.** El modelo opina; el código
   calcula el suyo con los números reales de la cuenta. Así un modelo optimista nunca relaja un límite.
2. **`direction_confidence` es la probabilidad del modelo para la dirección elegida**, no la estadística
   `confidence` de Jev. La documentación de Jev aclara que su `confidence` mide concentración, no
   probabilidad de acierto. La concentración se guarda aparte como `reflex_confidence` y es la que dispara
   la pausa por debajo de 0.60.
3. **`toxic_flow` es `null` sin libro de órdenes.** En backtests no hay libro histórico, así que la pregunta
   no se hace y el campo queda nulo, en vez de inventar un valor.
4. **Spot: `short` nunca abre posición.** Solo sirve para cerrar un largo. El tope de 2x del dueño queda
   reducido a 1x por el venue (`venue_max_leverage`).
5. **Fecha de corte de Jev.** TypeSafe no publica el corte de entrenamiento de Jev. Se usa su fecha de
   lanzamiento (2026-10-22, de `GET /v1/models`) como cota segura: un modelo no puede recordar lo que pasó
   después de publicarse.
6. **Kraken no tiene testnet de spot.** "Testnet primero" se cumple con el broker de paper sobre datos
   públicos reales y, antes de ir a live, con la bandera `validate=true` de Kraken (valida la orden sin enviarla).
7. **Estimación de tokens conservadora.** El snapshot se estima a 1 token por 2,5 caracteres. Medición real
   con Jev: estado más las cinco preguntas ≈ 600–900 tokens de entrada en total.
8. **Las políticas no pueden contener límites de riesgo.** `policy_store` rechaza cualquier archivo de política
   que traiga claves de riesgo, y el bucle nocturno nunca abre `config/risk.yaml`.
9. **Las alertas no son parte de la seguridad.** Si Telegram falla, el sistema sigue igual de protegido:
   ninguna decisión de riesgo depende de que un aviso llegue.

## Qué se mide
- Latencia de cada decisión (columna `latency_ms` del diario) y fallos por plazo (`misses`).
- Brier y tabla de fiabilidad sobre decisiones direccionales ya resueltas.
- Equity, comisiones, slippage por fill.
