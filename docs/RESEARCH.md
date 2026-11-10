# Investigación de mercado y finalistas

**Datos tomados el 2026-11-10 a las 17:05 UTC.** Archivo fuente: `data/research/snapshot_2026-11-10T1705Z.json`
(se regenera con `python -m reflexbot research`). Todo número de esta página sale de ese archivo o lleva su
propia fuente y fecha. Lo que es estimación está marcado como estimación. Esto no es asesoría financiera.

## Lo primero
- El régimen es **risk-off**. Nada en esta lista es una compra hoy; son montajes condicionales que la
  compuerta de código decide vela a vela.
- En la ejecución real de las 17:01 UTC, Jev marcó dirección `short` con confianza baja en BTC, ETH y SOL,
  y el sistema pausó riesgo nuevo en los tres.
- El universo operable es estrecho: Kraken solo tiene 44 pares contra USDT. Los tokens DeFi con ingresos
  claros (AAVE, UNI, HYPE y similares) cotizan contra USD, no contra USDT, y quedaron fuera.

## Régimen de mercado

| Señal | Valor | Lectura |
|---|---|---|
| BTC, último cierre diario | 75 309,5 USDT; −5,05 % vs media 50d; −1,59 % vs media 200d | Bajista |
| BTC, 30 días / 90 días | −9,25 % / +18,40 % | Corrección tras un rebote |
| BTC, caída desde máximo de 1 año | −28,96 % | |
| ETH, último cierre diario | 2 134,04 USDT; −10,01 % vs 50d; −7,69 % vs 200d | Bajista |
| ETH/BTC, 30 días | −6,17 % | Rotación hacia BTC, fuera de alts |
| Dominancia BTC / ETH | 59,71 % / 10,04 % | |
| Capitalización total | 2,44 billones USD; −6,25 % en 24 h | Venta amplia |
| Oferta de stablecoins | 292,0 mil M USD; −1,57 % en 7d; −4,60 % en 30d | Liquidez saliendo |
| Funding perpetuos Kraken (anualizado) | BTC +11,0 %; ETH −15,2 %; SOL −18,0 % | Cortos pagando en ETH y SOL |
| Interés abierto perpetuos Kraken | BTC 189 M USD; ETH 62 M; SOL 24 M | Solo Kraken, no el mercado entero |

Clasificación en código (`research/collect.py: classify_regime`): **risk_off**, puntuación −4 de 6 señales.
El funding anualizado es una conversión propia: Kraken informa el funding como importe absoluto en USD por
contrato y hora; se divide entre el precio de marca.

Precio en vivo a la hora de la toma (CoinGecko): BTC 72 886 USD, ETH 2 017,99, SOL 81,94.

### Macro y narrativa (fuentes secundarias, con fecha)
- **Fed**: mantuvo la tasa en 3,75 %–4,00 % el 28 de octubre de 2026, después de subirla un cuarto de punto en
  septiembre; dos miembros votaron por subir. El IPC de septiembre fue 3,7 % interanual.
  [CNBC, 2026-10-28](https://www.cnbc.com/2026/10/28/fed-interest-rate-decision-october-2026.html)
- **IPC de octubre**: se publicaba hoy, 10 de noviembre. No encontré el dato real; queda sin confirmar.
  [Previa, cryptonews.net](https://cryptonews.net/news/finance/33725615/)
- **Probabilidad de subida en diciembre**: 82,6 % según CME FedWatch al día siguiente de la reunión de octubre;
  puede haber cambiado. [StockTitan](https://www.stocktitan.net/articles/october-2026-cpi-inflation-report)
- **Causa de la caída de noviembre**: venta de acciones de chips e IA el 5 de noviembre, unos 1 080 M USD en
  liquidaciones en 24 h y unos 1 280 M USD de salidas netas de ETF de BTC entre el 2 y el 6 de noviembre.
  Las cifras varían entre medios.
  [Cryptonomist, 2026-11-06](https://en.cryptonomist.ch/2026/11/06/why-is-bitcoin-dropping-selloff/),
  [DailyForex, 2026-11-09](https://www.dailyforex.com/forex-technical-analysis/2026/11/bitcoin-forecast-9-november-2026/251623)

Lectura: tipos subiendo, liquidez de stablecoins cayendo y correlación con el Nasdaq. Es un entorno malo
para largos en alts. El sistema debe sobrevivirlo, no adivinarlo.

## Finalistas (6)

Fuentes de las tablas: CoinGecko (precio, capitalización, oferta), DefiLlama (comisiones, ingresos al
tenedor, TVL, direcciones activas), todo del snapshot de las 17:05 UTC. "Cap/comisiones" es capitalización
dividida entre comisiones de 30 días anualizadas.

| | SOL | ETH | BNB | XMR | AVAX | LINK |
|---|---|---|---|---|---|---|
| Precio (USD) | 81,94 | 2 017,99 | 670,72 | 555,51 | 8,18 | 10,16 |
| Capitalización (mil M) | 48,45 | 246,66 | 88,25 | 10,45 | 3,65 | 7,82 |
| 30 días | −26,8 % | −20,4 % | −11,2 % | +3,5 % | −24,4 % | −23,7 % |
| 1 año | −50,7 % | −42,7 % | −31,5 % | +39,6 % | −54,4 % | −36,6 % |
| Desde máximo histórico | −72 % | −59 % | −51 % | −30 % | −94 % | −81 % |
| Cap/comisiones | 227x | 1 011x | 619x | sin dato | 905x | sin dato |
| Cap/ingresos al tenedor | 2 103x | 3 263x | 6 193x | no aplica | 905x | sin dato |
| Comisiones 7d vs ritmo 30d | 0,79 | 0,54 | 0,92 | sin dato | 1,13 | sin dato |
| TVL de la cadena (mil M) | 4,99 | 40,86 | 5,08 | no aplica | 0,60 | no aplica |
| Cap/TVL | 9,7x | 6,0x | 17,4x | no aplica | 6,0x | no aplica |
| Direcciones activas 24 h | 2,71 M | 583 mil | 4,28 M | sin dato | 43,7 mil | sin dato |
| Media de 1 año | 2,30 M | 582 mil | 2,45 M | sin dato | 374 mil | sin dato |
| FDV/capitalización | 1,08 | 1,00 | 1,00 | 1,00 | 1,06 | 1,30 |

**No medido para ninguno**: concentración de tenedores y calendario exacto de desbloqueos. No encontré una
fuente primaria fechada, así que no pongo cifra.

### 1. SOL/USDT
- **Tesis**: es la L1 grande más barata por comisiones (227x contra 1 011x de ETH) y su uso está por encima
  de la media anual. Cayó 72 % desde el máximo con funding negativo.
- **Oferta y acumulación de valor**: 92,8 % de la oferta en circulación. Débil: la red emite cerca de
  60 000 SOL al día y quema unos 648 (dato de un medio secundario, sin fecha exacta). El tenedor solo
  recibe 1,9 M USD de 17,5 M USD de comisiones de 30 días; el resto va a validadores.
- **Catalizador confirmado**: ninguno con fecha.
- **Especulación**: Alpenglow sigue en testnet sin fecha de mainnet; la propuesta SIMD-547 de quemar una
  comisión base depende de Alpenglow y está en discusión.
  [KuCoin](https://www.kucoin.com/news/flash/solana-developers-deny-alpenglow-mainnet-activation-on-september-28),
  [TokenPost](https://tokenpost.com/news/technology/20991)
- **Caso bajista**: la inflación diluye más de lo que se quema, las comisiones dependen de la especulación
  on-chain y ya corren a 0,79 del ritmo mensual. Barato puede ser más barato.
- **Invalida la tesis**: comisiones de 30 días a menos de la mitad de hoy, direcciones activas bajo 1,5 M al
  día, o cierre bajo 65,5 USD.

### 2. ETH/USDT
- **Tesis**: la base DeFi más profunda (40,9 mil M de TVL, 6,0x cap/TVL), 40 % bajo su máximo anual.
- **Oferta y acumulación de valor**: 100 % en circulación. La quema devolvió 6,2 M USD en 30 días: 3 263x.
  A este precio no se justifica por flujo; es una apuesta a que la actividad vuelva.
- **Catalizador confirmado**: Glamsterdam se activó en la testnet Sepolia el 6 de octubre de 2026 (programado).
- **Especulación**: mainnet "en el cuarto trimestre de 2026", sin fecha. Ya se retrasó desde el primer semestre.
  [KuCoin](https://www.kucoin.com/news/flash/ethereum-s-glamsterdam-upgrade-enters-final-testnet-phase-ahead-of-q4-2026-mainnet-target)
- **Caso bajista**: las comisiones de 7 días corren a 0,54 del ritmo de 30 días. La actividad migra a L2 y la
  quema se encoge. Otro retraso de la actualización es probable.
- **Invalida la tesis**: un mes más de comisiones cayendo, Glamsterdam pasa a 2027, o cierre bajo 1 615 USD.

### 3. BNB/USDT
- **Tesis**: fuerza relativa en plena caída (−11 % en 30 días contra −20 % a −27 % del resto) y uso 74 % por
  encima de su media anual.
- **Oferta y acumulación de valor**: la quema trimestral número 37 retiró 1 646 196 BNB (unos 1 197 M USD) el
  19 de octubre de 2026 y dejó la oferta en 131,5 M. Es cerca de 1,25 % de la oferta por trimestre (cálculo
  propio). La fórmula depende del precio y de los bloques, no de los ingresos.
  [CryptoTicker](https://cryptoticker.io/en/bnb-quarterly-burn-supply-decline/)
- **Catalizador confirmado**: la quema es recurrente y verificable on-chain.
- **Especulación**: que la fuerza relativa continúe.
- **Caso bajista**: riesgo concentrado en una empresa. Un problema regulatorio o de solvencia de Binance
  golpea al token de forma directa. Algunos medios mencionan un hackeo reciente a un exchange grande; no lo
  pude confirmar con una fuente fiable.
- **Invalida la tesis**: pierde fuerza relativa frente a BTC en 30 días, evento específico de Binance, o
  cierre bajo 537 USD.

### 4. XMR/USDT
- **Tesis**: único activo del universo que sube en 30 días y en 1 año. Demanda de privacidad que no depende
  del ciclo de las alts.
- **Oferta y acumulación de valor**: sin comisiones que valorar; es dinero, no flujo. Emisión de cola baja
  (parámetro conocido del protocolo, no lo volví a verificar hoy).
- **Catalizador confirmado**: máximo histórico de 797,73 USD el 14 de enero de 2026.
  [Stocktwits](https://stocktwits.com/news-articles/markets/cryptocurrency/monero-breaks-another-record-high-nearing-700/cmUXETTR44A)
- **Especulación**: que las exclusiones de exchanges empujen demanda hacia otros canales.
- **Caso bajista**: Kraken ya lo retiró para clientes del Espacio Económico Europeo y 73 exchanges lo
  excluyeron en 2025. Si Kraken lo retira para tu región, la posición no se puede cerrar ahí.
  [KuCoin](https://www.kucoin.com/news/flash/monero-survives-73-exchange-delistings-in-2025-amid-privacy-gains-and-political-backing)
- **Invalida la tesis**: aviso de retirada del par en Kraken, fuerza relativa de 30 días negativa frente a
  BTC, o cierre bajo 444 USD.
- **Antes de operar**: confirma que tu cuenta de Kraken puede operar XMR en tu país.

### 5. AVAX/USDT (convicción más baja)
- **Tesis**: 94 % bajo su máximo, 6,0x cap/TVL, todas las comisiones se queman y el ritmo de comisiones sube
  (1,13 frente a 30 días).
- **Oferta y acumulación de valor**: 61,9 % del tope de 720 M en circulación; el resto se emite como
  recompensas de staking. La quema es de 0,33 M USD en 30 días: insignificante frente a 3,65 mil M.
- **Catalizador confirmado**: ninguno reciente encontrado.
- **Caso bajista**: las direcciones activas son 43,7 mil al día contra una media anual de 374 mil. El uso se
  hundió 88 %. Es la definición de trampa de valor.
- **Invalida la tesis**: direcciones activas bajo 100 mil durante 30 días más, comisiones a la baja, o cierre
  bajo 6,55 USD.

### 6. LINK/USDT
- **Tesis**: red de oráculos dominante, 81 % bajo su máximo, con un mecanismo que convierte ingresos en LINK.
- **Oferta y acumulación de valor**: la Chainlink Reserve tenía 6 047 498 LINK a finales de septiembre de 2026,
  tras sumar 373 791 LINK ese mes (unos 4,3 M USD). Anualizado es cerca de 0,7 % de la capitalización
  (estimación propia). FDV/capitalización de 1,30: el 23 % de la oferta aún no circula.
  [Radom](https://www.radom.com/insights/chainlink-reserve-nears-milestone-with-significant-accumulation-of-link-tokens)
- **Catalizador confirmado**: acumulación mensual de la reserva hasta septiembre. Sin dato de octubre.
- **Caso bajista**: la compra de la reserva es pequeña frente a la oferta que falta por liberar. Los 4,28 M USD
  de "Chainlink Staking" en DefiLlama son recompensas de staking, no ingresos del protocolo.
- **Invalida la tesis**: la reserva deja de acumular o vende, la oferta no circulante se libera más rápido,
  o cierre bajo 8,15 USD.

## Descartados y por qué
- **XRP**: capitalización de 66,4 mil M contra 0,04 M USD de comisiones en 30 días, y FDV/cap de 1,57.
  Parece sobrevalorado, pero en spot no se puede vender en corto.
- **VIRTUAL**: comisiones de 30 días de 0,18 M USD contra 10,3 M en 1 año. El ingreso se desplomó.
- **BERA**: −94 % en 1 año, FDV/cap de 1,55. Cuchillo cayendo.
- **TON, ADA, DOT, ATOM, ALGO**: comisiones despreciables frente a su capitalización.
- **DOGE, SHIB, TRUMP y demás memes**: sin fundamentales que evaluar.

## Cómo entra esto al sistema
`config/finalists.json` se compila a `config/schemas/<SÍMBOLO>.json`. El motor de estado añade al snapshot
el bloque de tesis (`bias`, `invalid_below`, `above_invalidation`), y el código bloquea entradas y cierra
la posición si el precio cruza `invalid_below`. Ese nivel está 20 % bajo el precio de hoy: es un respaldo
estimado, no un nivel probado.
