# LÓGICA DE TRADING — Remora Desk

> Par inseparable de `ARQUITECTURA.md`. **SPEC CONGELADA 28-ago-2026 19:45 Lima.**
> Todos los números viven en §13; el resto del documento explica su porqué.
> Origen: laboratorio SharkAnalysis (oro cent, en vivo) — cada regla cita su lección.

## 1. Filosofía (3 líneas)

1. **La pérdida se compra por adelantado**: solo debit spreads; máx pérdida = prima.
2. **Asegurar > dejar, condicionado a régimen**: cosechar en frenos, dejar correr
   con candados (escalera) en cohetes — el Cierre de Paul v1.
3. **El edge vive a 20–50× la fricción**: no perseguimos ticks; operamos
   movimientos de $0.20+ en SPY con fricción de centavos.

## 2. Universo y sesión

| | Valor |
|---|---|
| Símbolos | SPY (QQQ desde miércoles si 2 días estables — flag `SIMBOLOS`) |
| Ventana de ENTRADAS | 9:45 – 15:00 ET (8:45 – 14:00 Lima) |
| Gestión de salidas | continua hasta 15:50 ET |
| Flat total (EOD) | 15:50 ET — sin excepciones, sin overnight |
| Días | lun–jue del hackathon (viernes 4-sep solo cierre/submit) |

Sin entradas 9:30–9:45 (apertura caótica) ni después de 15:00 (theta de la
tarde + sin tiempo para desarrollar el movimiento).

## 3. Datos e indicadores

- Fuente única: velas **1m** de SPY (REST, feed IEX de paper). 5m/15m/30m se
  agregan localmente — una sola verdad, sin mezclas de feeds.
- **Solo velas CERRADAS.** Ningún cálculo ve la vela en formación (anti-lookahead,
  disciplina del laboratorio).
- Indicadores (fórmulas estándar): EMA20 y EMA50 sobre 5m; RSI14 (Wilder) sobre
  1m, 5m, 15m, 30m; ATR20 (Wilder) sobre 5m.

## 4. SEÑAL de entrada (determinista) — "cruce limpio adaptado"

Nivel de ruptura: `nivel_alto = max(high de las 6 velas 5m cerradas previas)`
(canal de 30 min). Espejo con `nivel_bajo = min(low ...)` para SHORT.

**Señal LONG** (todas simultáneas, sobre la vela 5m recién cerrada `t`):

| Condición | Regla | Lección de origen |
|---|---|---|
| S1 cruce dentro de la vela | `open[t] <= nivel_alto` y `close[t] > nivel_alto` | promesas: activar por cruce, no por estar arriba |
| S2 cierre limpio con cuerpo | `close[t] - nivel_alto >= 0.05` y `close[t] > open[t]` | knife-edges: nunca por $0.01 |
| S3 tendencia a favor | `EMA20(5m) > EMA50(5m)` | EP1 Asia: counter-momentum = veneno |
| S4 RSI multiframe alineado | `RSI5m ∈ [55, 75]` y `RSI15m >= 50` y `RSI30m >= 45` | la lectura multiframe de Paul; tope 75 = no llegar tarde |
| S5 vela-tope | `range[t] <= 2.2 × ATR20(5m)` | no perseguir velones de pánico |
| S6 anti-serrucho | sin señal previa (tomada o vetada) del mismo lado en las últimas 3 velas 5m | serrucho: no repicar el mismo cruce |

SHORT = espejo exacto (nivel_bajo, close<open, EMA20<EMA50, RSI ∈ [25,45]/≤50/≤55).
Sin señal ⇒ NO-GO y el ciclo termina (el LLM ni se consulta).

## 5. Gates del oficial de riesgo (orden de evaluación, todos deben pasar)

| # | Gate | Regla | Lección |
|---|---|---|---|
| G1 | HALT diario/semanal | sin HALT activo (§10) | freno de flota |
| G2 | Horario | dentro de ventana de entradas §2 | — |
| G3 | Presupuesto día | trades abiertos hoy < 8 | sobreoperar = fricción |
| G4 | Flota | posiciones vivas < 3 | techo-flota |
| G5 | Exposición | prima total viva + nueva ≤ $1,500 | 1.5% techo agregado |
| G6 | Cooldown anti-revancha | ≥ 30 min desde el último cierre en rojo | la lección del cruce limpio del 28-ago |
| G7 | Noticias | fuera de ±15 min de macro high-impact US (archivo `noticias.csv` semanal; sin archivo: bloquear 8:25–8:50 y 13:55–14:35 ET) | TODO-FTMO, nace aquí |
| G8 | Datos frescos | última vela 1m con antigüedad < 3 min | jamás decidir a ciegas |
| G9 | LLM disponible | respuesta válida (si no ⇒ PASS, no bloqueo) | la IA propone, no sostiene |
| G10 | Liquidez del contrato | §7 cumplido | teorema de la fricción |

## 6. Capa LLM (Featherless) — contrato estricto

**Entrada** (JSON): señal y dirección, valores S1–S6, estado G1–G8, IV y quote del
contrato candidato, últimos 5 titulares de `get_news`, últimos 3 registros del
diario, P&L del día.
**Salida** (JSON estricto): `{"decision":"OPEN"|"PASS","confianza":0-100,"razon":"≤400 chars"}`.
**Autoridad**: solo convierte GO en OPEN/PASS. No abre sin señal, no toca salidas,
no modifica tamaños. Timeout 20 s, 1 retry; fallo ⇒ PASS (`LLM_DOWN`).
El campo `razon` va íntegro al diario — es el alma del write-up y del video.

## 7. Selección de contrato (vehículo)

1. Expiración: la más cercana con **DTE 1–2** (nunca 0DTE, nunca > 2).
2. Patas (LONG): compra call primer strike ≤ spot; vende call **+$2** arriba
   (ancho fijo $2; SPY tiene strikes de $1). SHORT espejo con puts.
3. Precio del spread: `mid = mid(larga) − mid(corta)`; debit esperado ~$0.70–1.30.
4. **Gates de liquidez** (G10): bid > 0 en ambas patas; `(ask−bid)/mid` del spread
   neto ≤ 12%; OI ≥ 100 (o volumen del día ≥ 50 si OI no disponible).
   Si falla ⇒ PASS con `SIN_LIQUIDEZ` — el teorema de la fricción manda.

## 8. Órdenes (reglas de ejecución)

- Siempre **mleg LIMIT**, TIF DAY. JAMÁS market en opciones.
- Apertura: limit a `mid + 0.02` → 90 s sin fill → cancel → retry único a
  `mid + 0.04` → 90 s → cancel ⇒ PASS (`NO_FILL`). Nunca perseguir.
- Cierre (cosechas/SL/EOD): limit a `mid − 0.03` → 60 s → `mid − 0.06` → 60 s →
  limit al bid neto (agresivo final). El slippage vs mid se registra siempre.

## 9. Tamaño

`contratos = max(1, floor(500 / (debit × 100)))`, con techo de prima por trade
= **min(0.5% equity, $500)** y techo agregado $1,500 (G5).

## 10. SALIDAS (el Cierre de Paul embotellado) — monitor cada 60 s

Todo se mide en **% de la prima pagada**: `mark_pct = (mark − debit)/debit × 100`.
El pico (`peak_pct`) se persiste en `estado.json` (lección 11-ago: los picos
sobreviven reinicios).

**R1 — Escalera fib (candados; deja correr cohetes):**

| pico alcanzado ≥ | candado: cerrar si mark retrocede a |
|---|---|
| +40% | +15% |
| +65% | +40% |
| +100% | +65% |
| +161% | +100% |
| +261% | +161% |

**R2 — Freno del flotante (cosecha; Cierre de Paul v1):** armado si
`mark_pct ≥ +25`. Disparo si: (a) `peak_pct − mark_pct ≤ 10` y (b) 3 lecturas
consecutivas del monitor (~3 min) varían ≤ 4 puntos entre sí (velocidad ~0) y
(c) revalidación `RSI1m ≥ 68` (LONG; ≤ 32 SHORT) — "cuando hay frenos, van a
liquidar". Cerrar con razón `FRENO`.

**R3 — SL:** `mark_pct ≤ −50` ⇒ cerrar (`SL`). Escala con la prima — sin
knife-edge fijo.

**R4 — Time-stop (impulso muerto):** edad ≥ 3.5 h y `mark_pct ∈ (−15, +15)` ⇒
cerrar (`IMPULSO_MUERTO`) — el estancamiento oscilante paga theta sin tesis.

**R5 — EOD:** 15:50 ET cerrar todo (`EOD`).

**R6 — Watchdog diario:** P&L del día (realizado + flotante) ≤ **−1.5% equity**
⇒ cerrar TODA la flota + HALT hasta mañana (`WATCHDOG_DIA`).

**R7 — Circuit breaker del hackathon:** P&L acumulado ≤ **−4%** ⇒ HALT total;
solo Paul re-arma con sus manos (`WATCHDOG_SEMANA`).

Precedencia en el monitor: R6/R7 → R5 → R3 → R1 → R2 → R4.

## 11. Diario (evidencia = producto)

- `decisiones.jsonl` — cada ciclo, incluidos los PASS: ts/ET, señal S1–S6,
  gates G1–G10 con pass/fail, salida del LLM completa, acción, orden, equity.
- `eventos.jsonl` — fills, cierres con razón (R1–R7), slippage vs mid.
- `estado.json` — posiciones, picos, cooldowns, contadores; escritura atómica.

## 12. Lo que el agente JAMÁS hace

Vender prima desnuda · órdenes market en opciones · promediar/martingala ·
overnight · abrir sin señal GO · abrir con LLM caído · tocar el `.env` ·
operar 0DTE · exceder los techos de §5 aunque "la señal sea perfecta".

## 13. PARÁMETROS CONGELADOS (única fuente de verdad)

| Constante | Valor | | Constante | Valor |
|---|---|---|---|---|
| CANAL_VELAS_5M | 6 | | ESCALERA | tabla R1 |
| BUFFER_CRUCE | $0.05 | | FRENO_ARMADO_PCT | +25 |
| RSI5M_LONG | [55, 75] | | FRENO_BANDA_PTS | 10 |
| RSI15M_LONG_MIN | 50 | | FRENO_LECTURAS | 3 (≤4 pts entre sí) |
| RSI30M_LONG_MIN | 45 | | FRENO_RSI1M | ≥68 / ≤32 |
| VELA_TOPE_X_ATR | 2.2 | | SL_PCT | −50 |
| ANTI_SERRUCHO_VELAS | 3 | | TIMESTOP_H | 3.5 |
| CICLO_MIN | 10 | | TIMESTOP_BANDA | (−15, +15) |
| MONITOR_S | 60 | | EOD_ET | 15:50 |
| ENTRADAS_ET | 9:45–15:00 | | WATCHDOG_DIA_PCT | −1.5 |
| DTE | 1–2 | | WATCHDOG_TOTAL_PCT | −4 |
| ANCHO_SPREAD | $2 | | PRIMA_MAX_TRADE | min(0.5% eq, $500) |
| SPREAD_REL_MAX | 12% | | PRIMA_MAX_VIVA | $1,500 |
| OI_MIN | 100 | | MAX_POSICIONES | 3 |
| COOLDOWN_ROJO_MIN | 30 | | MAX_TRADES_DIA | 8 |
| NOTICIAS_VETO_MIN | ±15 | | LLM_TIMEOUT_S | 20 |

## 14. Métricas de éxito (proceso, no P&L)

1. 0 violaciones de gates en 4 días (el diario lo prueba).
2. 100% de decisiones journaleadas con razonamiento (incluidos PASS).
3. Pérdida máxima por trade jamás excede la prima (por construcción).
4. Slippage promedio de cierre ≤ $0.04 por spread.
5. El P&L que salga, sale — con R6/R7 el suelo del experimento es −4%.

## 15. Nota para el write-up (EN, jueves)

La historia: *a live-lab gold trader bottled his discipline — clean-cross
entries, multiframe RSI, fib-ladder locks, float-stall harvesting, and a risk
officer that can veto everything, including the AI.* Los jueces no verán un
bot que promete retornos: verán un desk que **no puede** hacerse daño y explica
cada decisión que tomó — y cada una que decidió no tomar.
