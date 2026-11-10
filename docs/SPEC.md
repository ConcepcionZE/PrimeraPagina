# Especificación (fase 1)

## Qué es
Un sistema de trading cripto 24/7 que investiga el mercado, elige unos pocos activos con tesis
asimétrica y los opera de forma automática. Empieza en **paper trading** y solo pasa a dinero real
cuando el dueño lo aprueba de forma explícita y existen las credenciales.

## Decisiones del dueño (2026-11-10)
| Tema | Decisión |
|---|---|
| Exchange | Kraken |
| Mercado | Spot (sin cortos, sin apalancamiento real) |
| Saldo paper | 10 000 USDT |
| Monedas prohibidas | Ninguna (`off_limits: []` en `config/default.yaml`) |
| REFLEX | Jev (TypeSafe), clave ya guardada en el entorno |

## Dos capas que nunca se mezclan
- **BRAIN** (Claude en la sesión de trabajo): investigación, estrategia, código, pruebas y revisión nocturna.
- **REFLEX** (Jev; Claude Haiku 5.5 como alternativa): en cada vela devuelve un juicio tipado
  (`regime`, `direction`, `toxic_flow`, `setup_quality`, `risk_state`) a partir de un estado compacto.

El código es dueño de todo umbral, tamaño, orden y efecto secundario. El REFLEX solo juzga.

## Requisitos funcionales
1. **Motor de estado**: convierte velas y libro de órdenes en un snapshot numérico de menos de 400 tokens,
   con marcas de tiempo causales. Calcula en código mid, spread, desbalance, volatilidad, inventario,
   exposición y drawdown. Detecta datos faltantes, feeds viejos, errores del exchange y desfase de reloj
   antes de cualquier orden.
2. **Capa de riesgo dura**: 2 % de la cuenta en riesgo por operación, 5 % de pérdida diaria máxima,
   3 posiciones abiertas, 2x de apalancamiento, kill switch al 15 % de drawdown. Se revisa antes de cada orden.
   Ningún modelo puede saltársela y ninguna llamada a un modelo es necesaria para frenar una orden.
3. **Compuerta**: entra solo si `setup_quality >= 2`, `direction_confidence > 0.80` y `risk_state == safe`.
   Tamaño por Kelly fraccional sobre probabilidad calibrada, máximo un cuarto de Kelly.
   Si la confianza del REFLEX baja de 0.60 o el régimen es `crisis`: pausa riesgo nuevo y avisa al dueño.
4. **Latencia**: se mide en cada decisión; si una decisión llega tarde, esa vela se salta.
5. **Backtests**: solo walk-forward, con comisiones y slippage, comparados contra mantener BTC.
   Un modelo solo se evalúa con datos posteriores a su fecha de corte.
6. **Auto-mejora nocturna**: lee cada fill y cada fallo, mide calibración (Brier), propone cambios,
   los pasa por las mismas compuertas, versiona, conserva rollback y **nunca** promueve sola.
7. **Runtime**: Docker Compose con reinicio automático, health check y alertas a Telegram.
8. **Aprobaciones**: sin permiso se puede leer datos, escribir código, probar, hacer backtests y paper trading.
   Requieren el visto bueno explícito del dueño: órdenes reales, mover fondos, claves con permisos de trading
   y desplegar a producción.

## Fuera de alcance en esta versión
- Órdenes reales (el adaptador existe, pero está bloqueado por la compuerta de live).
- Cortos y perpetuos.
- Pares que no coticen contra USDT en Kraken.

## Criterio de "hecho"
Cada requisito tiene pruebas automáticas que lo cubren (`pytest`), el sistema corre un ciclo real de paper
contra Kraken y Jev, y `PROGRESS.md` separa lo implementado, lo probado, lo simulado y lo bloqueado.
