# Plan (fase 3) y estado de las seis fases

| Fase | Entregable | Estado |
|---|---|---|
| 1. Spec | `docs/SPEC.md` | Hecho |
| 2. Arquitectura | `docs/ARCHITECTURE.md` | Hecho |
| 3. Plan | este archivo | Hecho |
| 4. Construcción con pruebas | `src/reflexbot/`, `tests/` | Hecho; ver nota |
| 5. Revisión | `docs/REVIEW.md` | Hecho |
| 6. Entrega | `Dockerfile`, `docker-compose.yml`, `docs/RUNBOOK.md`, compuerta de aprobación | Hecho en paper; live bloqueado a propósito |

Nota sobre "test-first": las pruebas se escribieron junto con cada módulo y antes de ejecutar nada contra
el exchange o el modelo. No se escribieron estrictamente antes que el código.

## Orden de construcción
1. Contratos tipados y motor de estado (causalidad y salud de datos).
2. Capa de riesgo dura.
3. Compuerta y tamaño de posición.
4. Adaptadores del REFLEX detrás de una sola interfaz (Jev, Haiku, reglas).
5. Broker de paper con costos reales y adaptador de Kraken.
6. Backtest walk-forward con benchmark de BTC y guarda de fecha de corte.
7. Diario, calibración, almacén de políticas, bucle nocturno.
8. Runner 24/7, alertas, CLI, Docker.
9. Investigación con datos en vivo, finalistas y esquemas.
10. Ejecuciones reales: ciclo de paper, backtests, replay de Jev.

## Lo siguiente
1. Dejar el paper corriendo 30 días en un servidor con Docker para juntar decisiones reales con libro de órdenes.
2. Con 100 o más decisiones resueltas, el bucle nocturno empieza a proponer calibración y umbrales.
3. Añadir pares contra USD si el dueño acepta USD como moneda de cotización (amplía mucho el universo).
4. Solo después, y con aprobación: clave de trading, validación de órdenes y conexión del adaptador live.
