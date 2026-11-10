# reflexbot

Agente de trading cripto 24/7 para Kraken spot. Arranca en **paper trading** (10 000 USDT simulados) y no
puede enviar una orden real hasta que el dueño abra la compuerta de aprobación.

Lee primero [`PROGRESS.md`](PROGRESS.md): qué está hecho, qué sigue y por qué se tomó cada decisión.

## Cómo está armado
- **BRAIN**: investigación, estrategia, código, pruebas y revisión nocturna (trabajo de la sesión de Claude).
- **REFLEX**: Jev (TypeSafe) juzga cada vela y devuelve un resultado tipado. Claude Haiku 5.5 y un motor de
  reglas en código están detrás de la misma interfaz.
- **Código**: dueño de todo umbral, tamaño, orden y límite de riesgo. El modelo solo juzga.

## Uso rápido

```bash
pip install -e ".[dev,haiku]"
python -m pytest -q                       # pruebas
python -m reflexbot status                # política activa y estado de la compuerta de live
python -m reflexbot research              # datos de régimen con fecha y fuente
python -m reflexbot backtest --tf 4h      # walk-forward de reglas contra mantener BTC
python -m reflexbot run --once            # un ciclo de paper con datos reales
docker compose up -d --build              # runtime 24/7 (necesita .env; ver .env.example)
```

## Documentos
| Archivo | Contenido |
|---|---|
| [`PROGRESS.md`](PROGRESS.md) | Estado, próximos pasos y registro de decisiones |
| [`docs/SPEC.md`](docs/SPEC.md) | Qué debe hacer el sistema |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Flujo, módulos y razones de diseño |
| [`docs/PLAN.md`](docs/PLAN.md) | Las seis fases y su estado |
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | Régimen de mercado y finalistas, con fuentes fechadas |
| [`docs/REVIEW.md`](docs/REVIEW.md) | Resultados, revisión final y qué puede estar mal |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Operación, alertas, kill switch, compuerta de live, rollback |

## Seguridad
- Las claves viven fuera del repositorio (`.env` está en `.gitignore`). Nunca se imprimen ni se registran.
- La clave de Kraken para live debe ser solo de trading, sin retiros y con lista blanca de IP.
- Los límites de riesgo están en `config/risk.yaml` y solo los cambia el dueño.

Nada de este repositorio es asesoría financiera. Los umbrales de partida son hipótesis, no evidencia de ventaja.
