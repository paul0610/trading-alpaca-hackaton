# ARQUITECTURA — Remora Desk

> Agente autónomo de opciones sobre SPY para el Alpaca AI Trading Agents Hackathon
> (lablab.ai, 28-ago → 4-sep-2026). Cuenta paper `PA3YMXK3ZBP1`.
> **SPEC CONGELADA 28-ago-2026 19:45 Lima.** Cambios solo con acta de Paul.
> Implementación: Opus, siguiendo los incrementos I1–I6 de este documento.
> La lógica de trading vive en `LOGICA_TRADING.md` (par inseparable de este archivo).

## 1. Concepto

Un desk de tres capas — la disciplina de un laboratorio real de trading en vivo,
embotellada:

1. **Capa Señal (el trader)** — determinista. Lee velas de SPY (1m/5m/15m/30m),
   computa cruce limpio + RSI multiframe + régimen. Emite GO/NO-GO.
2. **Capa Decisión (la IA)** — un LLM (Featherless) recibe el snapshot de hechos
   y decide OPEN/PASS **solo cuando la señal es GO**, escribiendo su razonamiento
   en el diario. La IA propone dentro del carril; jamás abre sin señal ni
   salta un gate.
3. **Capa Riesgo (el oficial de riesgo)** — determinista, sin IA. Gates duros
   pre-orden + watchdog de flota + salidas automáticas. Manda sobre todo.

Pérdida máxima por trade = prima pagada (debit spreads). Matemáticamente
imposible perder más de lo prepagado.

## 2. Decisiones congeladas

| # | Decisión | Valor | Por qué |
|---|---|---|---|
| D1 | Universo | SPY (QQQ opcional desde miércoles si 2 días estables) | liquidez máxima, foco |
| D2 | Vehículo | Debit spreads verticales (call spread LONG / put spread SHORT), DTE 1–2, nunca 0DTE | riesgo definido, theta tolerable |
| D3 | Ciclo de decisión | cada 10 min alineado a cierre de vela 5m; monitor de salidas cada 60 s | autonomía visible sin sobreoperar |
| D4 | Sin overnight | flat 15:50 ET todos los días | sin gap risk, sin riesgo de asignación |
| D5 | LLM del loop | Featherless (API OpenAI-compatible, $25 gratis del hackathon, elegibilidad premio partner); proveedor pluggable por env | no gasta créditos Claude; fallo LLM ⇒ PASS |
| D6 | Ejecución | **REST Trading API directo con stdlib urllib** (cero dependencias) | CLI oficial está en alpha ("should not be depended on in production" — advertencia de Alpaca); REST es auditable y estable |
| D7 | Cumplimiento MCP/CLI | **MCP server montado y usado**: pre-flight matinal, reporte diario y demo del video corren vía Claude Code + `alpaca-mcp-server@2.3.0` | requisito "MCP o CLI" cumplido con MCP; descarte del CLI documentado en README |
| D8 | Dependencias del motor | **SOLO stdlib de Python** (urllib, json, threading, math) | auditable por jueces, sin supply-chain, sin conflictos con el Python del laboratorio |
| D9 | Prima máx/trade | min(0.5% equity, $500) | riesgo acotado, ~8–15 trades de margen diario |
| D10 | Demo URL del submit | Dashboard estático generado por `reporte.py` → `docs/index.html` → GitHub Pages | sin servidor que mantener |
| D11 | Idiomas | Specs internas ES; README + write-up + dashboard EN | jueces leen EN; Paul opera ES |

## 3. Componentes y flujo

```
                    ┌─────────────────────────────────────────────┐
                    │              agente.py (loop)               │
                    │  reloj mercado → ciclo 10 min → orquesta    │
                    └──────┬──────────────────────────┬───────────┘
                           │ cada 10 min              │ hilo cada 60 s
                           v                          v
   ┌────────────── CICLO DE ENTRADA ─────────┐   ┌── MONITOR DE SALIDAS ──┐
   │ datos.py    velas 1m SPY → agrega 5/15/30│   │ cartera.py: marks de   │
   │ senal.py    cruce limpio + RSI + régimen │   │ cada spread (REST)     │
   │ gates.py    G1..G10 (oficial de riesgo)  │   │ salidas.py: escalera + │
   │ llm.py      Featherless: OPEN/PASS + why │   │ freno + SL + timestop  │
   │ contratos.py cadena → strike/DTE/liquidez│   │ + EOD + watchdog día   │
   │ ejecutor.py  orden mleg limit (REST)     │   │ ejecutor.py cierra     │
   └────────────┬─────────────────────────────┘   └──────────┬─────────────┘
                v                                            v
        diario.py  decisiones.jsonl + eventos.jsonl + estado.json (atómico)
                │
                v
        reporte.py → docs/index.html (dashboard EN, GitHub Pages)

   Aparte, interactivo (no en el loop):  Claude Code + MCP alpaca
   → pre-flight matinal, inspección de cuenta, material del video
```

### Módulos (`motor/`)

| Archivo | Responsabilidad | Notas |
|---|---|---|
| `config.py` | lee `.env`, expone constantes congeladas de LOGICA_TRADING §13 | única fuente de parámetros |
| `datos.py` | REST Market Data: velas 1m históricas + snapshot; agrega 5/15/30m localmente | SOLO velas cerradas (anti-lookahead) |
| `senal.py` | indicadores (EMA, RSI Wilder, ATR) + señal cruce limpio | funciones puras, testeables |
| `gates.py` | G1..G10 como funciones puras `(estado, ctx) -> (bool, razon)` | 1 test unitario por gate mínimo |
| `llm.py` | POST Featherless (OpenAI-compat, urllib), JSON estricto, timeout 20 s, 1 retry; fallo ⇒ PASS | proveedor/modelo por env |
| `contratos.py` | REST cadena de opciones: expiry DTE 1–2, strikes, ancho, liquidez, precio del spread | dry-run posible (I3) |
| `ejecutor.py` | órdenes mleg LIMIT vía REST `/v2/orders`, cancel, cierre escalonado | NUNCA market; reglas en LOGICA §8 |
| `cartera.py` | posiciones vivas, marks, picos, P&L día/semana | picos persisten en `estado.json` |
| `salidas.py` | escalera fib + freno del flotante + SL + time-stop + EOD + watchdogs | corre en el hilo de 60 s |
| `diario.py` | append JSONL + escritura atómica de `estado.json` (tmp + replace) | sobrevive kill -9 |
| `agente.py` | main: reloj, ciclos, hilo monitor, HALT flags, modo sombra/real | punto de entrada único |
| `reporte.py` | journal → `docs/index.html` (equity, trades, decisiones con razonamiento) | correr al cierre diario |

### Ciclo de entrada (cada 10 min, en :x5+15 s tras cierre de vela 5m)

1. `datos` refresca velas → 2. `senal` computa GO/NO-GO + dirección →
3. si NO-GO: journalear y dormir → 4. `gates` evalúa G1..G10; cualquier fallo
⇒ journalear PASS con razón → 5. `llm` decide OPEN/PASS con razonamiento →
6. si OPEN: `contratos` elige spread; si no hay contrato líquido ⇒ PASS →
7. `ejecutor` manda mleg limit con reglas de reintento → 8. journalear todo
(incluidos los PASS: **las decisiones de no operar también son decisiones**).

## 4. Configuración (`.env`, NUNCA al repo)

```
ALPACA_API_KEY / ALPACA_SECRET_KEY / ALPACA_PAPER_TRADE=true   (ya puestas)
FEATHERLESS_API_KEY=        (Paul la reclama y la pone — $25 del hackathon)
FEATHERLESS_MODEL=          (Opus elige del catálogo en I3: DeepSeek-V3 /
                             Llama-3.3-70B / Qwen-72B; queda parametrizado)
MODO=sombra                 (sombra = decide y journalea SIN ordenar | real)
SIMBOLOS=SPY                (miércoles: SPY,QQQ si 2 días estables)
```

`lanzar.cmd`: activa loop con reinicio ante crash (patrón launch_cent.cmd).
El modo `real` SOLO lo lanza Paul con sus manos.

## 5. Cumplimiento del hackathon (mapa para el write-up)

| Requisito | Dónde se cumple |
|---|---|
| Agente autónomo | `agente.py` + `lanzar.cmd`: cero manos tras el lanzamiento; diario prueba cada decisión |
| Trading API | REST `paper-api.alpaca.markets` (órdenes, posiciones, cuenta) + Market Data API |
| MCP server | `.mcp.json` monta `alpaca-mcp-server@2.3.0`; pre-flight + reporte + demo en video vía Claude Code |
| Opciones obligatorias | 100% de las órdenes son spreads de opciones (mleg) |
| Paper + cuenta nueva $100k | `PA3YMXK3ZBP1`, `ALPACA_PAPER_TRADE=true` |
| Repo público MIT | este repo (LICENSE MIT; `.env` gitignored — verificado) |
| Demo URL | GitHub Pages: dashboard de `reporte.py` |
| Write-up 1 página | destilar de estos dos MDs (EN): AI logic + risk gates + infra |

## 6. Incrementos de implementación (para Opus)

| Inc | Contenido | DoD (definición de hecho) |
|---|---|---|
| I1 | `config` + `datos` + `senal` | script `validar_senal.py` imprime las señales de los últimos 5 días hábiles de SPY con velas reales; revisión ocular de Paul |
| I2 | `gates` + tests | 1+ test por gate incluyendo bordes (knife-edges); todos verdes |
| I3 | `llm` + `contratos` (dry-run) | contra cadena real de paper: elige contrato y precio SIN ordenar; Featherless responde JSON válido en <20 s; modelo elegido y anotado |
| I4 | `ejecutor` + `cartera` | lunes pre-apertura: 1 spread de 1 contrato en paper — abrir, ver fill, cerrar; slippage vs mid registrado en eventos.jsonl |
| I5 | `salidas` + `agente` + `diario` en **modo sombra sobre replay** | replay de las velas del viernes 28-ago completo: diario coherente, escalera/freno/SL/timestop disparan donde deben; kill del proceso a mitad ⇒ reanuda sin duplicar estado |
| I6 | `reporte` + `lanzar.cmd` + README(EN) + LICENSE MIT + GitHub Pages | dashboard visible online; README con quickstart de 3 comandos |

**Timeline**: I1–I3 sábado, I4 lunes 8:15–8:30 Lima, I5 domingo, I6 domingo/lunes.
Lunes 8:30 Lima: Paul lanza `MODO=sombra` en vivo 30 min → si limpio, relanza
`MODO=real`. Track record lun–jue. Jueves noche: submit.

### Reglas para Opus (no negociables)

1. **Solo stdlib** en `motor/` — cero pip installs.
2. **NUNCA** tocar `.env`, imprimir keys, ni commitear secretos (gitignore ya
   lo bloquea; verificar con `git check-ignore .env` antes de cada push).
3. Commits a nombre de Paul, **SIN** trailer `Co-Authored-By`.
4. Anti-lookahead: señales SOLO con velas cerradas; ningún indicador ve la vela
   en formación.
5. Parámetros congelados de `LOGICA_TRADING.md` §13: prohibido cambiarlos sin
   acta de Paul. Si un parámetro resulta inviable en la práctica, reportar, no
   ajustar en silencio.
6. Errores REST: reintento x1 con backoff 2 s; si persiste, el ciclo se pierde
   y se journalea `API_DOWN` — jamás abrir a ciegas. Las SALIDAS sí insisten
   (proteger > abrir).
7. Todo timestamp interno en **ET** (mercado) con epoch UTC en el diario.

## 7. Riesgos conocidos y mitigación

| Riesgo | Mitigación |
|---|---|
| Featherless caído/lento | timeout 20 s + 1 retry ⇒ PASS; salidas nunca dependen del LLM |
| Orden mleg rechazada | journalear razón; 2 reintentos de precio (LOGICA §8); luego PASS |
| Rate limit REST (200/min) | cadencia real ~10 req/min; sin riesgo |
| Fin de semana sin mercado | I5 valida con replay del viernes; I4 valida fills el lunes pre-apertura |
| Crash del proceso | `lanzar.cmd` reinicia; `estado.json` atómico restaura picos/cooldowns/contadores |
| JSON del LLM malformado | parseo estricto + 1 reintento ⇒ PASS con razón `LLM_MALFORMED` |
| P&L de 4 días es ruido | el submit argumenta proceso: gates, diario, riesgo prepagado (LOGICA §15) |
