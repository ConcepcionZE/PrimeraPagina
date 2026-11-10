# Runbook

## Arrancar en paper (24/7)

```bash
cp .env.example .env          # completar JEV_API_KEY; opcional TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
docker compose up -d --build  # servicios: trader (bucle de paper) y nightly (revisión diaria 00:20 UTC)
docker compose ps             # trader debe quedar "healthy"
docker compose logs -f trader
```

`restart: unless-stopped` más el arranque automático de Docker (`systemctl enable docker`) devuelven ambos
servicios tras una caída o un reinicio. El estado (cuenta de paper, diario, kill switch) vive en el volumen
`botdata` y sobrevive a los reinicios.

Sin Docker:

```bash
pip install -e ".[dev,haiku]"
python -m reflexbot run            # bucle
python -m reflexbot run --once     # un solo ciclo
```

## Comandos

| Comando | Para qué |
|---|---|
| `python -m reflexbot status` | Política activa, compuerta de live, contadores, equity |
| `python -m reflexbot health` | Sale con 0 si el heartbeat tiene menos de 5 minutos |
| `python -m reflexbot research` | Snapshot fechado de régimen y fundamentales |
| `python -m reflexbot backtest --tf 4h` | Walk-forward de las reglas contra mantener BTC |
| `python -m reflexbot model-backtest --provider jev` | Replay del modelo solo en datos posteriores a su corte |
| `python -m reflexbot nightly` | Revisión nocturna manual |
| `python -m reflexbot promote v2 --by <nombre> --confirm PROMOTE` | **Solo humano**: activar una política que pasó las compuertas |
| `python -m reflexbot rollback --by <nombre>` | Volver a la política anterior |
| `python -m pytest -q` | Pruebas de regresión |

## Alertas y qué hacer

| Alerta | Significado | Acción |
|---|---|---|
| `DATA UNHEALTHY` | Feed viejo, datos faltantes, error del exchange o desfase de reloj | Nada se opera. Revisar red y hora del host (`chronyc tracking`). |
| `PAUSED new risk` | Confianza del REFLEX < 0.60 o régimen `crisis` | No se abren posiciones en ese símbolo. Revisar mercado; se reanuda solo. |
| `KILL SWITCH ON` | Drawdown ≥ 15 % | Se cierra todo y queda bloqueado. Ver "Kill switch". |
| `HALT new risk - ledger mismatch` | El diario y el broker no coinciden | No abrir nada. Comparar `fills` del diario con las posiciones. |
| `ORDER FAILED` / `EXIT FAILED` | El exchange rechazó o falló | Se reintenta en el siguiente ciclo para salidas; revisar liquidez. |
| `CYCLE ERROR` | Excepción en el bucle | El bucle sigue; revisar logs. |

## Kill switch
Se activa solo, queda guardado en `data/risk_state.json` y sobrevive reinicios. Para rearmarlo, después de
entender qué pasó:

```python
from reflexbot.risk import RiskEngine, RiskLimits
RiskEngine(RiskLimits(), "data/risk_state.json").reset_kill_switch("I-RESET-THE-KILL-SWITCH")
```

## Cambiar límites de riesgo
Editar `config/risk.yaml` a mano y reiniciar. Ningún proceso automático escribe ese archivo.

## Compuerta de aprobación para ir a live (fase 6)
Nada de esto está hecho ni se hará sin tu visto bueno explícito.

1. Revisar al menos 30 días de paper: `status`, informes en `data/nightly/`, Brier y equity contra mantener BTC.
2. Crear en Kraken una clave **solo de trading**: sin retiros, con lista blanca de IP. Guardarla fuera del repo
   (en `.env` del servidor o en un gestor de secretos). Nunca pegarla en un chat ni imprimirla.
3. Probar cada tipo de orden con `KrakenLive.validate_order` (Kraken valida sin ejecutar).
4. Escribir a mano el archivo `LIVE_APPROVAL` con el texto `I APPROVE LIVE TRADING <versión de política>`.
5. Poner `TRADING_MODE=live` en `.env`.
6. Conectar `KrakenLive` en el runner. **En esta versión el runner solo opera el broker de paper**; ese cambio
   de código es parte de la aprobación y lleva sus propias pruebas.

`python -m reflexbot status` muestra qué condiciones faltan.

## Rollback
- De política: `python -m reflexbot rollback --by <nombre>`.
- De código: `git revert` del commit y `docker compose up -d --build`.
- De emergencia: `docker compose stop trader` (las posiciones de paper quedan como están).
