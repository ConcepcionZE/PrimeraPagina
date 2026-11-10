# Resultados y revisión final (fase 5)

Fecha: 2026-11-10. Todos los números salen de ejecuciones reales de este repositorio; los archivos están en
`data/research/`.

## Lo primero: ninguna estrategia supera a mantener BTC

| Prueba | Periodo | Estrategia | Mantener BTC | Operaciones | Veredicto |
|---|---|---|---|---|---|
| Reglas en código, velas de 4 h, walk-forward, 6 tramos | 2026-09-01 a 2026-10-31 | −5,47 % | −0,31 % | 4 (0 ganadoras) | **No supera a BTC** |
| Reglas en código, velas diarias, walk-forward, 6 tramos | 2025-09-17 a 2026-09-11 | 0,00 % | −34,29 % | 0 | Sin operaciones: no es evidencia |
| Jev, velas de 1 h, 3 símbolos, después de su fecha de corte | 2026-10-22 a 2026-11-10 | 0,00 % | −10,89 % | 0 | Sin operaciones: no es evidencia |
| Jev, velas de 1 h, 7 símbolos, después de su fecha de corte | 2026-10-22 a 2026-11-10 | 0,00 % | −10,89 % | 0 | Sin operaciones: no es evidencia |

Comisiones (0,26 % taker de Kraken) y slippage incluidos en todo. Quedarse fuera mientras BTC cae no es una
ventaja: es no haber operado. La única prueba con operaciones reales perdió contra BTC.

Archivos: `backtest_rules_4h.json`, `backtest_rules_1d.json`, `backtest_jev_1h.json`,
`backtest_jev_1h_universe.json`.

## Qué mostró Jev en 3 143 decisiones

| Medida | Valor |
|---|---|
| Fallos (errores o fuera de plazo) | 0 |
| Latencia | p50 191 ms; p95 236 ms; máximo 480 ms (plazo: 2 000 ms) |
| Decisiones `long` / `short` / `neutral` | 267 / 1 407 / 1 469 |
| Acierto direccional a 12 velas (1 613 llamadas) | 49,5 % |
| Brier | 0,2605 (decir siempre 50 % da 0,25) |
| Tramo de confianza 0,6–0,8 (453 llamadas) | confianza media 0,651; acierto real 48,3 % |
| Probabilidad máxima asignada a `long` | 0,62 |
| Llamadas `long` con `setup_quality >= 2` | 1 |
| Régimen `mean_reverting` | 3 068 de 3 143 |
| Velas con confianza del REFLEX ≥ 0,60 | 117 de 3 143 |

Lectura:
1. **Como infraestructura funciona**: rápido, sin fallos y siempre dentro del esquema.
2. **Como pronosticador de dirección no mostró habilidad** en esta muestra. Sus probabilidades fueron peores
   que una moneda y estuvo sobreconfiado en el tramo alto.
3. **Con los umbrales de partida el sistema no puede operar con Jev.** Nunca pasó de 0,62 en `long`, y el
   umbral es 0,80. La pausa por confianza menor a 0,60 se activó en el 96 % de las velas.

Diagnóstico en muestra (`jev_threshold_sensitivity_insample.json`; no es walk-forward ni evidencia): incluso
bajando la confianza a 0,55, la calidad a 1 y quitando el piso de 0,60, salen 4 operaciones y +0,60 %. Es
ruido. No cambié ningún umbral: son los valores de partida del dueño y cambiarlos mirando esta misma muestra
sería sobreajuste.

## Revisión final

**¿La ventaja es orgánica o viene de incentivos?** No hay ventaja medida. En los finalistas, el valor que
llega al tenedor es bajo: SOL devuelve 1,9 M USD de 17,5 M de comisiones mientras emite mucho más de lo que
quema, y la quema de BNB sigue una fórmula de precio y bloques, no ingresos.

**¿El catalizador ya está en el precio?** Probablemente sí en los conocidos: Glamsterdam lleva meses
anunciado y retrasado, y la quema de BNB es trimestral y pública. Alpenglow no tiene fecha.

**¿El valor llega al token?** Poco y de forma indirecta en los seis. XMR no tiene flujo alguno.

**¿Sobrevive a costos y slippage?** La única estrategia que operó (reglas, 4 h) pagó 73,63 USDT en comisiones
en 4 operaciones y perdió 5,47 %. Con 0,52 % de costo de ida y vuelta, una ventaja pequeña no sobrevive.

**¿Algún límite duro quedó en manos de un modelo?** No. Verificado con pruebas:
- El `risk_state` efectivo es el peor entre el del modelo y el calculado en código.
- El kill switch, la pérdida diaria, el tope de posiciones, el apalancamiento y el riesgo por operación
  están en `risk.py`, que no llama a ningún modelo.
- Una política no puede contener límites de riesgo y el bucle nocturno no puede promover nada.
- Si el modelo no responde, la vela se salta; frenar una orden nunca espera a un modelo.

**¿Sesgo de anticipación, fugas o fallos sin probar?**
- Causalidad probada: cambiar datos futuros no altera ninguna decisión pasada; el snapshot rechaza velas
  que cierran después de su marca de tiempo; las órdenes se ejecutan en la apertura de la vela siguiente.
- Fuga de memoria del modelo: el replay de un modelo falla si se le dan datos anteriores a su corte.
- Sesgo de supervivencia: sí existe. El universo son los pares que Kraken lista hoy.
- Sin probar: el adaptador live contra Kraken real, Haiku contra la API real, Telegram contra la API real,
  `docker compose up` (no hay daemon de Docker en este entorno; el archivo se validó con `docker compose config`),
  comportamiento con libro de órdenes histórico (no existe en backtest, así que `toxic_flow` fue nulo).

**Fallo real encontrado y corregido durante la construcción**: el chequeo de desfase de reloj contaba como
desfase el tiempo que tardaban los feeds. El sistema no operó (comportamiento seguro), pero el diagnóstico
era falso. Corregido en `runner.py` con prueba de regresión.

## WHAT COULD I BE WRONG ABOUT?

1. **La premisa entera.** Puede que un modelo de decisión general mirando 20 indicadores horarios no pueda
   predecir la dirección a 12 horas. 49,5 % de acierto en 1 613 llamadas apunta a eso. Si es así, ninguna
   calibración lo arregla y el REFLEX debería juzgar otra cosa (por ejemplo, calidad de ejecución o régimen),
   no dirección.
2. **La muestra de Jev es de 19 días y un solo régimen** (venta generalizada). Los horizontes de 12 velas se
   solapan, así que las 1 613 llamadas equivalen a muchas menos observaciones independientes. La conclusión
   "sin habilidad" puede ser tan frágil como lo sería una conclusión "con habilidad".
3. **La fecha de corte de Jev es una suposición.** Usé su fecha de lanzamiento. Si se reentrena de forma
   continua, la guarda no protege.
4. **El diseño del estado.** Quizá el snapshot no le da a Jev lo que necesita (no hay libro histórico, ni
   funding, ni contexto entre activos). Un mal resultado puede ser culpa de mis preguntas y no del modelo.
5. **Los umbrales de partida pueden ser incompatibles con una pregunta de tres opciones.** Exigir más de 0,80
   en una de tres respuestas es mucho más duro que en una de dos. Puede que el esquema deba cambiar antes
   que el umbral.
6. **Las reglas en código pueden estar simplemente mal hechas.** Cuatro operaciones no distinguen una mala
   idea de mala suerte.
7. **El modelo de slippage en backtest es una estimación** (3 pb base más impacto por participación). En
   pares poco líquidos como AVAX o LINK contra USDT el costo real puede ser mayor.
8. **Los datos fundamentales vienen de agregadores** (CoinGecko, DefiLlama), no de las cadenas. Un error de
   clasificación ahí cambia los múltiplos. La macro viene de prensa, no de la Fed ni del BLS directamente.
9. **Los niveles de invalidación son arbitrarios** (20 % bajo el precio). No están probados.
10. **El régimen puede girar rápido.** Si el IPC de octubre salió bajo, la lectura risk-off de hoy envejece
    en horas, y no pude confirmar ese dato.

Prefiero un sistema que sobrevive a uno que parece rentable: este, tal como está, sobrevive porque casi no
opera. Eso es seguro, pero todavía no es una estrategia.
