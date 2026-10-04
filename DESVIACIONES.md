# DESVIACIONES — implementación vs. spec congelada

> Regla 5 de `ARQUITECTURA.md` §6: *"Si un parámetro resulta inviable en la
> práctica, reportar, no ajustar en silencio."* Este archivo es ese reporte.
> Nada de `LOGICA_TRADING.md` §13 se cambió. Lo que sigue son (A) correcciones
> de defectos donde la spec se contradecía a sí misma, (B) parámetros que la
> spec usaba en prosa pero no tabulaba, y (C) hallazgos que **necesitan acta de
> Paul** y por ahora están implementados tal cual dice la spec.

---

## A. Correcciones de contradicciones internas

### A1 — El orden LLM ↔ contrato era circular *(bloqueante)*

`ARQUITECTURA.md:89-91` evaluaba G1–G10 (paso 4) y consultaba al LLM (paso 5)
**antes** de elegir contrato (paso 6). Pero:

- G10 (`LOGICA:69`) es liquidez **del contrato**;
- el input del LLM (`LOGICA:73`) incluye "IV y quote del **contrato candidato**";
- G5 (`LOGICA:64`) necesita la **prima nueva**, que depende de contrato y qty.

Los tres dependen de algo que en ese punto no existe. **Orden implementado:**

```
señal GO → G1 G2 G3 G4 G6 G7 G8 → contrato → tamaño → G5 G10
         → LLM (G9 = resultado de esa etapa) → revalidación → orden
```

Ningún gate cambió de definición; solo se partió la evaluación en dos etapas.
Ver `motor/gates.py` y `motor/agente.py:ciclo_entrada`.

### A2 — El flat de 15:50 era incompatible con su propia escalera de cierre

`LOGICA:96` define el cierre como 60 s + 60 s + limit agresivo al bid. Si R5
(`LOGICA:132`) dispara **a las** 15:50, es imposible estar plano **a las** 15:50.

**Implementado:** `EOD_INICIO_ET = 15:45` (constante nueva). `EOD_ET = 15:50`
sigue siendo el objetivo de estar plano. Además, al entrar en la ventana de EOD
se **cancelan las órdenes abiertas** antes de cerrar: una apertura pendiente a
las 15:45 podía llenar después del flat y dejar posición overnight, que es
justo lo que D4 prohíbe. Ver `motor/salidas.py:r5_eod`.

### A3 — Fórmula de tamaño (§9)

`contratos = max(1, floor(500 / (debit × 100)))` tenía tres defectos:

1. **`500` hardcodeado** donde la propia línea dice `min(0.5% equity, $500)`.
   Con equity < $100k el literal excede el techo.
2. **`max(1, …)` convierte un techo de riesgo en un piso**: abre 1 contrato
   aunque el presupuesto no alcance. Implementado: `qty == 0 ⇒ PASS`.
3. **Se dimensionaba al mid pero se llena al retry.** §8 permite llenar a
   `mid + 0.04`: 5 contratos × $0.04 × 100 = **$520 sobre un tope de $500**.
   Implementado: se dimensiona contra el peor precio alcanzable.

Además, el techo agregado de G5 descuenta la **prima pendiente** (órdenes
mandadas y no llenadas). Sin eso, dos ciclos seguidos duplican exposición.

### A4 — S6 y la cadencia de 10 minutos

El ciclo corre cada 10 min (D3) pero S6 mira "las últimas 3 velas 5m". Si la
señal solo se computara al actuar, S6 consultaría un historial con huecos y
sería ciego a la mitad de los cruces.

**Implementado:** el cálculo es **sin estado**. Cada ciclo recomputa la serie
completa de señales sobre todas las velas 5m cerradas; S6 ve el historial
íntegro. El agente *actúa* solo sobre la última vela y solo en su franja de
10 min. No cambia §13; cambia dónde vive el historial. `CICLO_MIN` sigue en 10.

### A5 — Zona horaria sin `tzdata`

Windows no trae la base IANA y `tzdata` es un paquete de PyPI: usarlo violaría
la Regla 1 ("cero pip installs"). **Implementado:** las reglas de DST de US
Eastern directamente en `motor/reloj.py`, y **epoch UTC como única
representación canónica** (ET es siempre una vista derivada). Esto hace el
replay histórico determinista y hace imposible que un cruce de DST corrompa el
estado. Cubierto por `tests/test_reloj.py`, incluidos los bordes exactos de
entrada y salida de DST.

### A6 — Idempotencia de órdenes

`lanzar.cmd` reinicia ante crash (`ARQUITECTURA:106`) pero `ARQUITECTURA:161`
solo prometía restaurar picos/cooldowns/contadores — **nada sobre órdenes**. Un
crash entre el POST y el apunte duplica la posición al reiniciar.

**Implementado** (`motor/ejecutor.py`): intención durable con `fsync` **antes**
del POST · `client_order_id` determinista · consulta al broker antes de
reenviar · cancelación **confirmada** antes del siguiente precio · reconciliación
al arrancar · lock de proceso único · lo mismo en cierres y EOD.

Detalle relevante: si no se puede **confirmar** la cancelación, el ejecutor
**no manda el segundo precio**. Dos limits vivos del mismo spread es el
escenario exacto que este módulo existe para impedir.

### A7 — Schema del diario imposible de cumplir

`LOGICA:144` exige G1–G10 y salida del LLM **cada ciclo**, pero un NO-GO
termina antes de los gates (`LOGICA:54`). **Implementado:** estado
`NOT_EVALUATED` por gate y `llm: null`. Así el registro distingue *"el gate
pasó"* de *"el gate no se llegó a evaluar"*, que no es lo mismo.

### A8 — G7 noticias: cobertura y fail-closed

El fallback de `LOGICA:66` bloquea 8:25–8:50 y 13:55–14:35 ET. Pero la ventana
de entradas es 9:45–15:00 (`LOGICA:20`): **el tramo matinal es inerte** (cae
fuera de la ventana) y **las 10:00 ET quedaban descubiertas**, siendo la
segunda franja macro más cargada del día (ISM, JOLTS, Confianza).

**Implementado:** se **añade** 9:55–10:15 ET y se conserva 13:55–14:35. El
`noticias.csv` es la fuente primaria pero las ventanas fallback **siempre
aplican**: el archivo solo puede *añadir* vetos, nunca quitarlos. Un CSV
incompleto (el fallo más probable, porque lo llena un humano con prisa) degrada
a la protección genérica en vez de abrir un hueco silencioso.

**`noticias.csv` no existe todavía** → hoy corre el fallback. Ver
`noticias.csv.example`.

### A9 — Knife-edge de S2 en coma flotante *(hallazgo nuevo de esta sesión)*

S2 exige `close − nivel >= 0.05`. En binario, `500.05 − 500.00` puede dar
`0.049999999999997`, y la condición falla **en su propio borde**. La lección de
origen de S2 es "knife-edges: nunca por $0.01" — un borde que se voltea según
la representación binaria es exactamente el bug que la regla quería evitar.

**Implementado:** `config.EPS = 1e-9` en la comparación de S2 y S1 (y espejo
SHORT). Probado en `tests/test_senal.py` con el caso de $0.05 exacto.

### A10 — R2 necesita RSI1m, pero el monitor era solo-marks

`LOGICA:123` exige revalidación `RSI1m ≥ 68`, pero `ARQUITECTURA:53-54`
especificaba el monitor de 60 s como solo-marks. **Implementado:** el monitor
refresca velas 1m. Y si el RSI1m no está disponible, **R2 no dispara**:
cosechar sin la condición (c) sería inventarse una comprobación que no se hizo.

### A11 — Reintentos contradictorios

`LOGICA:95` dice "retry único" en apertura; `ARQUITECTURA:158` dice "2
reintentos". **Implementado:** `LOGICA` §8 manda (es la spec de trading): dos
precios en total (`mid+0.02`, luego `mid+0.04`), o sea **un retry**.

### A12 — Fallos de integración encontrados en la revisión ejecutable

La primera suite validaba funciones puras, pero no varios estados reales del
broker. La revisión posterior corrigió, sin cambiar parámetros de estrategia:

- `pending_cancel` ya no cuenta como terminal; puede llenar hasta que el
  broker confirme la cancelación;
- un `filled_qty` parcial se adopta y el residuo queda persistido/protegido;
- los cierres posteriores versionan el `client_order_id` cuando la ronda
  anterior terminó cancelada, en vez de atascarse adoptando un ID muerto;
- el lock es del sistema operativo y se libera tras crash (un archivo huérfano
  ya no impide el reinicio);
- reconciliación ambigua, fills huérfanos o posiciones en modo no-real bloquean
  el arranque con código 4;
- EOD reintenta una cancelación no confirmada y solo cancela órdenes `remora-`;
- quote incompleta falla cerrado, y quote/liquidez/tamaño/G1–G8 se revalidan
  después del LLM antes de reservar exposición;
- R7 usa `equity` de Alpaca sin volver a sumar el flotante que ya contiene.

También se completó el input explícito de §6: IV de ambas patas y los últimos
cinco titulares de Alpaca News llegan al snapshot del LLM.

---

## B. Parámetros usados en prosa pero ausentes de §13

Están en `config.py` marcados `# [DESV]`, con su valor tomado de la prosa de la
spec — no se inventó ninguno:

| Constante | Valor | Fuente en la spec |
|---|---|---|
| `EMA_RAPIDA` / `EMA_LENTA` | 20 / 50 | `LOGICA` §3 |
| `RSI_PERIODO` / `ATR_PERIODO` | 14 / 20 | `LOGICA` §3 |
| `FRESCURA_DATOS_MIN` | 3 min | `LOGICA` §5 G8 |
| `VOLUMEN_MIN_ALTERNO` | 50 | `LOGICA` §7.4 |
| bandas RSI SHORT | [25,45] / ≤50 / ≤55 | `LOGICA` §4 |
| offsets de orden | 0.02 / 0.04 / 0.03 / 0.06 | `LOGICA` §8 |
| timeouts de orden | 90 s / 60 s | `LOGICA` §8 |
| `DEBITO_ESPERADO` | 0.70–1.30 | `LOGICA` §7.3 |
| `EOD_INICIO_ET` | 15:45 | derivado (ver A2) |
| `EPS` | 1e-9 | nuevo (ver A9) |

**Sugerencia para el acta:** §13 dice ser "única fuente de verdad"; hoy no lo
es. Convendría absorber esta tabla.

### Otras decisiones de implementación

- **Solo sesión regular (9:30–16:00 ET).** Las velas de pre/post market son
  finísimas en IEX y agregarlas al canal de 30 min y a EMA/RSI/ATR distorsiona
  los indicadores. La spec no lo decía explícitamente.
- **ATR de S5 es el de la vela *previa*.** Si usara el de la propia vela, un
  velón inflaría su propio umbral (~+10% con Wilder de 20) y el filtro se
  auto-anularía. `LOGICA:50` es ambiguo; esta lectura es la anti-lookahead.
- **`MODO=replay` no se expone todavía.** I5 (`ARQUITECTURA:130`) lo exige,
  pero sin fixtures históricos de quotes de opciones y respuestas LLM el
  orquestador solo podría usar datos vivos y llamarlos replay. Se bloquea esa
  falsa garantía; `validar_senal.py` reproduce únicamente la capa de señal.
- **Liquidez vía `/v1beta1/options/snapshots`**: `/v2/options/contracts` **no
  expone volumen diario**, solo `open_interest` (que en muchas cuentas llega
  nulo). Sin el snapshot, la vía alterna de G10 ("volumen ≥ 50") sería letra
  muerta y el gate bloquearía todo por `OI_BAJO`.

---

## C. Pendientes de acta y comprobaciones de lanzamiento

### C1 — La escalera fib R1 es mayormente inalcanzable *(el más grave)*

Un vertical de ancho $2 vale **como máximo $2.00**. El techo de `mark_pct` es
`(ancho − débito)/débito × 100`. Con el débito esperado de §7.3:

| débito | techo de `mark_pct` | peldaños vivos de R1 |
|---|---|---|
| $0.70 | **+185.7%** | +40, +65, +100, +161 |
| $1.00 | **+100.0%** | +40, +65, +100 *(en el borde)* |
| $1.30 | **+53.8%** | **solo +40** |

El peldaño **+261% no es alcanzable jamás** dentro del rango de la propia spec.
Peor: acercarse al techo exige estar profundo ITM cerca de expiración, y con
flat a las 15:50 eso casi no ocurre.

**Estado:** implementado **literal** (§13 congelada). Lo que se añadió es
evidencia: `peldanos_alcanzables()` se journalea en cada apertura, así el
diario muestra cuánta escalera estaba viva en cada trade.

**Propuesta para el acta:** re-expresar los peldaños como % del **beneficio
máximo posible** (`ancho − débito`) en vez de % de la prima. Con eso la
escalera completa vuelve a tener sentido para cualquier débito.

> Hallazgo estructural relacionado (probado en `tests/test_salidas.py`): todo
> candado de R1 está más de `FRENO_BANDA_PTS` (10) por debajo de su peldaño, así
> que **R1 y R2 nunca pueden dispararse a la vez**. La precedencia R1 → R2 del
> §10 nunca llega a arbitrar nada.

### C2 — ¿Dispara la señal alguna vez? **RESUELTO**

Ejecutado el 1-sep-2026 sobre 5 sesiones reales de SPY: **40** cruces S1–S5,
**22** GO después de S6 y **9 GO accionables** por la cadencia real de 10 min y
G2 (1.80 por sesión antes de G1–G10). La señal no está muerta. El validador
ahora separa la frecuencia bruta de la realmente accionable.

### C3 — Nivel de opciones de la cuenta **RESUELTO**

`smoke_test.py` ejecutado el 1-sep-2026: cuenta `PA3YMXK3ZBP1` ACTIVE,
`options_approved_level=3` y `options_trading_level=3`. D2 es viable.

El preflight `validar_contrato.py` también recorrió la cadena real sin órdenes:
el call spread 762/764 DTE 1 pasó G10 (débito 0.92, spread relativo 2.2%); el
put spread 763/761 fue bloqueado correctamente por G10 (19.0% > 12%).

### C4 — Otros puntos menores pendientes de decisión

- **Métrica §14.4** (slippage de cierre ≤ $0.04) está diseñada para fallar: el
  peldaño 2 del cierre ya es −$0.06 y el 3 es el bid neto. Solo la cumple quien
  llena en el primer intento.
- **R6 (−1.5% = −$1,500) casi coincide con la exposición máxima agregada de G5
  ($1,500)**: desde flotante puro solo dispara con la cartera a cero. En la
  práctica solo se alcanza con pérdidas realizadas acumuladas.
- **R4 time-stop (3.5 h)** alcanza solo a las entradas anteriores a ~12:20 ET
  (≈la mitad de la ventana). No es código muerto, es cobertura parcial. Bajarlo
  a 2.0 h es juicio de Paul, no un defecto.
- **Riesgo del feed IEX**: S1/S2 son reglas de precisión de $0.05 sobre
  highs/lows que en IEX difieren del consolidado.
- **`D1`/`LOGICA:19` "QQQ desde miércoles si 2 días estables"** quedó sin
  sentido con el timeline colapsado.

---

## D. Hallazgos del lanzamiento en vivo (miércoles 2-sep-2026)

Los dos primeros arranques en `MODO=real` destaparon un defecto de producción y
un parámetro que la validación demostró mal calibrado. Se registran aquí con el
mismo criterio que el resto: qué se rompió, cómo se vio desde fuera, y qué se
cambió.

### D1 — La capa IA devolvía 403 y era invisible *(defecto, no acta)*

Sondeando `motor/llm.py` por su ruta real antes de la apertura, Featherless
respondía `403` con cuerpo `error code: 1010`. Ese código no es de la API: es de
**Cloudflare**, que rechaza al cliente por fingerprint antes de que la petición
llegue al modelo. La causa era el User-Agent por defecto de `urllib`
(`Python-urllib/3.13`). Comprobado contra `/v1/models`:

```
(por defecto urllib)  -> 403  error code: 1010
curl/8.4.0            -> 200  OK (21912 modelos)
RemoraDesk/1.0        -> 200  OK (21912 modelos)
```

Lo grave no es el 403 sino **cómo se veía desde fuera**. El diseño fail-closed
de §6 convierte cualquier fallo del LLM en PASS, así que el agente habría pasado
la sesión entera journaleando PASS tras PASS — indistinguible de *"el modelo
decidió no operar"*. Con la key correcta y el modelo correcto, el resultado
habría sido cero trades y cero P&L sin una sola señal de alarma.

**Implementado:** constante `USER_AGENT` y cabecera explícita en la petición.
Sigue siendo stdlib puro. Verificado por la ruta real del motor: DeepSeek-V3
responde JSON válido en ~2.5 s.

**Lección que generaliza:** un fallo que degrada a la decisión por defecto es
más peligroso que uno que rompe ruidosamente. `LLM_HTTP_403` sí quedaba en el
campo `error` del diario — pero solo aparece en ciclos que llegan a consultar al
LLM, y un NO-GO nunca llega. Merece un chequeo de arranque que ejercite la ruta
IA una vez y falle fuerte, en vez de esperar al primer GO del día.

### D2 — `CICLO_MIN` 10 → 5 *(**acta de Paul**, 2026-09-02)*

`validar_senal.py` sobre 5 sesiones (317 velas 5m):

```
cruces S1-S5          34
GO (con S6)           18
GO accionables         8     <- se perdía el 55%
frecuencia bruta      3.60 / sesión
frecuencia accionable 1.60 / sesión
```

Una señal nace al cerrar una vela 5m, pero el ciclo de 10 min solo mira uno de
cada dos cierres: **diez GO válidos de dieciocho morían por cadencia, no por
criterio**. Con dos sesiones restantes y el P&L como primer criterio de judging,
1.60 accionables/sesión — antes todavía de G1–G10 y del veto del LLM — dejaba la
expectativa por debajo de un trade al día.

**Autorizado por Paul:** `CICLO_MIN = 5`, alineado a cada cierre de vela 5m.

No relaja **ninguna** condición S ni **ningún** gate: no cambia qué califica como
oportunidad, solo con qué frecuencia el motor se asoma a mirar. El sobre de
riesgo es idéntico — G3 (8 trades/día), G4 (3 posiciones), G5 ($1,500 de prima
viva) y los techos por trade siguen mandando igual. Se descartó explícitamente
tocar S6 o las bandas de RSI: ahí se compraría actividad a cambio de calidad.

**Bug latente que el cambio destapó:** `proximo_ciclo` tenía la cadencia
**hardcodeada** en la aritmética de alineación —
`salto = (10 - ((t.minute - 5) % 10)) % 10` — mientras el bucle de espera sí
leía `config.CICLO_MIN`. Cambiar la constante sola no habría movido nada: el
planificador habría seguido despertando en la rejilla de 10 min y el acta se
habría dado por aplicada sin efecto. Corregido para derivar el paso de config.

Cubierto por `tests/test_agente.py::TestProximoCiclo` (4 casos: la rejilla de 5
cae en cada cierre 5m, la de 10 conserva el comportamiento original, 5 produce
exactamente el doble de ciclos que 10, y nunca se devuelve un instante pasado en
los bordes del desfase). Verificado que el test **falla** contra el código con
el 10 hardcodeado: no es un test decorativo.
