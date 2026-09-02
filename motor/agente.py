# -*- coding: utf-8 -*-
"""agente.py — punto de entrada unico del desk (ARQUITECTURA §3).

Reloj de mercado -> ciclo de entrada cada 10 min (alineado a :x5+15 s) +
hilo monitor de salidas cada 60 s. HALT flags. Modo sombra / real.

ORDEN DEL CICLO DE ENTRADA (corregido respecto de ARQUITECTURA §3; ver
DESVIACIONES.md y el docstring de gates.py):

    senal GO -> G1 G2 G3 G4 G6 G7 G8 -> contrato -> tamano -> G5 G10
    -> LLM (G9 es su resultado) -> revalidacion -> orden

`MODO=sombra` recorre el ciclo entero y journalea, pero `ejecutor` nunca hace
POST. Es el modo por defecto y el unico que este codigo puede lanzar solo:
`MODO=real` lo pone Paul con sus manos.
"""
import argparse
import sys
import threading
import time

from motor import (cartera, config, contratos, datos, diario, ejecutor, gates,
                   llm, reloj, salidas, senal)

# Ventana de historial que se descarga cada ciclo. EMA50 sobre 5m necesita 50
# velas y RSI14 sobre 30m necesita 15 velas (7.5 h): con menos de dos sesiones
# los indicadores no calientan y todo seria NO-GO por `CALENTANDO`.
DIAS_HISTORIAL = 6


def proximo_ciclo(ahora):
    """Siguiente instante :x5+15 s (05:15, 15:15, 25:15... de cada hora)."""
    t = reloj.et(ahora)
    salto = (10 - ((t.minute - 5) % 10)) % 10
    objetivo = ahora + salto * 60 - t.second - t.microsecond / 1e6 + 15
    while objetivo <= ahora:
        objetivo += config.CICLO_MIN * 60
    return objetivo


class Agente(object):
    def __init__(self, cfg, cliente, dia):
        self.cfg = cfg
        self.cli = cliente
        self.diario = dia
        self.cartera = cartera.Cartera(cliente, dia)
        self.ejec = ejecutor.Ejecutor(cliente, dia, cfg)
        self.lock_estado = threading.RLock()
        self.parar = threading.Event()
        self._eod_cancelado = False
        self._sesion_eod = self.cartera.sesion
        self.noticias, self.noticias_problema = gates.cargar_noticias(
            config.RAIZ / 'noticias.csv')

    # -- utilidades --------------------------------------------------------
    def mercado_abierto(self):
        r = self.cli.reloj()
        if not r.ok:
            return None, r.error
        return bool((r.datos or {}).get('is_open')), ''

    def _velas(self, simbolo, ahora):
        inicio = ahora - DIAS_HISTORIAL * 86400
        v1, err = datos.velas_1m(self.cli, simbolo, inicio, ahora)
        if err:
            return None, err
        return {
            1: v1,
            5: datos.agregar(v1, 5, ahora),
            15: datos.agregar(v1, 15, ahora),
            30: datos.agregar(v1, 30, ahora),
        }, ''

    # ------------------------------------------------------------------
    # CICLO DE ENTRADA
    # ------------------------------------------------------------------
    def ciclo_entrada(self, simbolo):
        ahora = time.time()
        registro = {'simbolo': simbolo, 'modo': self.cfg.modo,
                    'ciclo_et': reloj.iso_et(ahora)}

        err = self.cartera.refrescar_cuenta()
        if err:
            return self._pass(registro, 'API_DOWN', 'cuenta: %s' % err[:120])

        velas, err = self._velas(simbolo, ahora)
        if err:
            return self._pass(registro, 'API_DOWN', 'velas: %s' % err[:120])
        if len(velas[5]) < config.EMA_LENTA:
            return self._pass(registro, 'CALENTANDO',
                              'solo %d velas 5m' % len(velas[5]))

        # --- 1. SENAL -----------------------------------------------------
        s = senal.ultima(velas[5], velas[15], velas[30])
        registro['senal'] = s.dict()
        registro['frescura_min'] = round(datos.frescura_min(velas[1], ahora), 2)
        if not s.go:
            registro['gates'] = gates.cuadro([])
            registro['llm'] = None       # NO-GO termina antes: no hay LLM
            return self._pass(registro, 'NO_GO', s.motivo)

        # --- 2. GATES PREVIOS (no necesitan contrato) ---------------------
        with self.lock_estado:
            ctx = gates.Contexto(
                ahora=ahora, halt=self.cartera.halt,
                trades_hoy=self.cartera.trades_hoy,
                posiciones_vivas=len(self.cartera.vivas()),
                ultimo_cierre_rojo=self.cartera.ultimo_cierre_rojo,
                frescura_min=datos.frescura_min(velas[1], ahora),
                noticias=self.noticias,
                noticias_problema=self.noticias_problema,
                equity=self.cartera.equity,
                prima_viva=self.cartera.prima_viva(),
                prima_pendiente=self.cartera.prima_pendiente)
        ok_previos, veredictos = gates.evaluar_previos(ctx)
        if not ok_previos:
            registro['gates'] = gates.cuadro(veredictos)
            registro['llm'] = None
            fallidos = [v.razon for v in veredictos if not v.ok]
            return self._pass(registro, 'GATE_FAIL', ';'.join(fallidos))

        # --- 3. CONTRATO --------------------------------------------------
        spots, err = datos.snapshot_indices(self.cli, [simbolo])
        spot = spots.get(simbolo) or (velas[5][-1].c if velas[5] else 0.0)
        spread, razon = contratos.elegir(self.cli, simbolo, s.direccion, spot,
                                         ahora)
        if spread is None:
            registro['gates'] = gates.cuadro(veredictos)
            registro['llm'] = None
            return self._pass(registro, 'SIN_CONTRATO', razon)
        registro['contrato'] = spread.dict()

        # --- 4. TAMANO ----------------------------------------------------
        qty, prima_nueva, det_tam = gates.tamano(
            self.cartera.equity, spread.mid, ctx.prima_viva,
            ctx.prima_pendiente)
        registro['tamano'] = det_tam
        if qty < 1:
            registro['gates'] = gates.cuadro(veredictos)
            registro['llm'] = None
            return self._pass(registro, 'SIN_TAMANO',
                              det_tam.get('razon', 'qty=0'))

        # --- 5. GATES DE CONTRATO (G5, G10) -------------------------------
        veredictos.append(gates.g5_exposicion(prima_nueva, ctx.prima_viva,
                                              ctx.prima_pendiente))
        veredictos.append(gates.g10_liquidez(spread))
        registro['gates'] = gates.cuadro(veredictos)
        if not all(v.ok for v in veredictos):
            registro['llm'] = None
            fallidos = [v.razon for v in veredictos if not v.ok]
            return self._pass(registro, 'GATE_FAIL', ';'.join(fallidos))

        # --- 6. LLM (G9 es su resultado, no un gate previo) ---------------
        titulares, err_noticias = datos.titulares(self.cli, simbolo, 5)
        registro['titulares'] = titulares
        if err_noticias:
            registro['titulares_error'] = err_noticias[:120]
        decision = llm.decidir(
            self.cfg, self._snapshot(s, spread, qty, ctx, veredictos,
                                     titulares, err_noticias))
        registro['llm'] = decision.dict()
        veredictos.append(gates.Veredicto(
            'G9', gates.PASA if not decision.error else gates.FALLA,
            decision.error, {'decision': decision.accion}))
        registro['gates'] = gates.cuadro(veredictos)
        if not decision.abre:
            return self._pass(registro, 'LLM_PASS',
                              decision.error or 'la IA decidio PASS')

        # --- 7. REVALIDACION antes de mandar ------------------------------
        # Entre la senal y este punto pasaron llamadas REST y hasta 20 s de
        # LLM. Se revalida lo que pudo caducar; abrir con un contexto vencido
        # es exactamente lo que G8 existe para impedir.
        ahora2 = time.time()
        fresco, err_quote = contratos.refrescar_spread(self.cli, spread)
        if err_quote:
            registro['revalidacion'] = {'error': err_quote[:120]}
            return self._pass(registro, 'REVALIDACION_FALLIDA',
                              'QUOTE:%s' % err_quote[:100])
        spread = fresco
        with self.lock_estado:
            ctx2 = gates.Contexto(
                ahora=ahora2, halt=self.cartera.halt,
                trades_hoy=self.cartera.trades_hoy,
                posiciones_vivas=len(self.cartera.vivas()),
                ultimo_cierre_rojo=self.cartera.ultimo_cierre_rojo,
                frescura_min=datos.frescura_min(velas[1], ahora2),
                noticias=self.noticias,
                noticias_problema=self.noticias_problema,
                equity=self.cartera.equity,
                prima_viva=self.cartera.prima_viva(),
                prima_pendiente=self.cartera.prima_pendiente)
        _, reval = gates.evaluar_previos(ctx2)
        qty_max, _, tam_reval = gates.tamano(
            ctx2.equity, spread.mid, ctx2.prima_viva, ctx2.prima_pendiente)
        prima_nueva = (spread.mid + config.OFFSET_APERTURA_2) * 100.0 * qty
        reval.extend([
            gates.g5_exposicion(prima_nueva, ctx2.prima_viva,
                                ctx2.prima_pendiente),
            gates.g10_liquidez(spread),
        ])
        registro['revalidacion'] = {
            'gates': gates.cuadro(reval), 'tamano': tam_reval,
            'contrato': spread.dict(), 'qty_planeada': qty,
            'qty_max_actual': qty_max,
        }
        fallidos = [v.razon for v in reval if not v.ok]
        if qty > qty_max:
            fallidos.append('TAMANO_EXCEDE_QUOTE_FRESCA')
        if fallidos:
            return self._pass(registro, 'REVALIDACION_FALLIDA',
                              ';'.join(fallidos))

        # --- 8. ORDEN -----------------------------------------------------
        referencia = '%s-%s-%d' % (simbolo, s.direccion, int(s.ts))
        with self.lock_estado:
            # Ultimo guard atomico con la reserva: el monitor puede activar
            # HALT o cambiar la flota mientras se refrescaba la quote.
            finales = [gates.g1_halt(gates.Contexto(
                           ahora=time.time(), halt=self.cartera.halt)),
                       gates.g4_flota(gates.Contexto(
                           ahora=time.time(),
                           posiciones_vivas=len(self.cartera.vivas()))),
                       gates.g5_exposicion(
                           prima_nueva, self.cartera.prima_viva(),
                           self.cartera.prima_pendiente)]
            if not all(v.ok for v in finales):
                registro['guard_final'] = [v.dict() for v in finales]
                return self._pass(
                    registro, 'REVALIDACION_FALLIDA',
                    ';'.join(v.razon for v in finales if not v.ok))
            self.cartera.prima_pendiente += prima_nueva
        try:
            res, err = self.ejec.abrir(spread, qty, self.cartera.sesion,
                                       referencia)
        finally:
            with self.lock_estado:
                self.cartera.prima_pendiente = max(
                    0.0, self.cartera.prima_pendiente - prima_nueva)
        registro['orden'] = res
        if res.get('filled'):
            with self.lock_estado:
                pos = self.cartera.registrar_apertura(
                    spread, res.get('qty', qty), res['precio'], referencia)
            registro['accion'] = 'OPEN'
            registro['posicion'] = pos.ref
            registro['escalera_alcanzable'] = salidas.peldanos_alcanzables(
                res['precio'], spread.ancho)
            self.diario.evento('APERTURA', ref=referencia, qty=qty,
                               precio=res['precio'],
                               escalera=registro['escalera_alcanzable'])
            return self.diario.decision(registro)
        if res.get('sombra'):
            registro['accion'] = 'OPEN_SOMBRA'
            registro['escalera_alcanzable'] = salidas.peldanos_alcanzables(
                res['precio'], spread.ancho)
            return self.diario.decision(registro)
        return self._pass(registro, res.get('razon', 'NO_FILL'), err)

    def _pass(self, registro, razon, detalle=''):
        registro.setdefault('gates', gates.cuadro([]))
        registro.setdefault('llm', None)
        registro['accion'] = 'PASS'
        registro['razon'] = razon
        registro['detalle'] = detalle
        return self.diario.decision(registro)

    def _snapshot(self, s, spread, qty, ctx, veredictos, titulares=(),
                  titulares_error=''):
        """Los hechos que ve el LLM (§6). Solo hechos: ni consejos ni sesgos."""
        recientes = self.diario.decisiones()[-3:]
        return {
            'signal': {'direction': s.direccion, 'conditions': s.cond,
                       'data': s.datos},
            'risk_gates': {v.gate: v.estado for v in veredictos},
            'candidate_spread': spread.dict(),
            'latest_news': list(titulares)[:5],
            'news_error': titulares_error[:120],
            'size_contracts': qty,
            'premium_at_risk_usd': round(spread.mid * 100 * qty, 2),
            'max_profit_usd': round(spread.ganancia_max * 100 * qty, 2),
            'account': {'equity': ctx.equity,
                        'open_positions': ctx.posiciones_vivas,
                        'trades_today': ctx.trades_hoy,
                        'day_pnl_pct': round(self.cartera.pnl_dia_pct(), 3)},
            'recent_journal': [{'accion': r.get('accion'),
                                'razon': r.get('razon'),
                                'et': r.get('ts_et')} for r in recientes],
        }

    # ------------------------------------------------------------------
    # MONITOR DE SALIDAS (hilo, cada 60 s)
    # ------------------------------------------------------------------
    def ciclo_monitor(self):
        ahora = time.time()
        minutos = reloj.minutos_et(ahora)

        # Antes de cualquier otra cosa en la ventana de EOD: matar las ordenes
        # pendientes. Una apertura viva a las 15:45 puede llenar despues del
        # flat y dejar una posicion overnight — justo lo que D4 prohibe.
        sesion = reloj.fecha_et(ahora)
        if sesion != self._sesion_eod:          # dia nuevo: se re-arma
            self._sesion_eod = sesion
            self._eod_cancelado = False
        if minutos >= config.EOD_INICIO_ET and not self._eod_cancelado:
            _, err_cancel = self.ejec.cancelar_abiertas('EOD')
            if err_cancel:
                self.diario.evento('EOD_CANCEL_PENDIENTE', error=err_cancel)
            else:
                self._eod_cancelado = True

        with self.lock_estado:
            vivas = self.cartera.vivas()
        if not vivas:
            return
        self.cartera.refrescar_cuenta()
        _, err = self.cartera.marcar()
        if err:
            self.diario.evento('MONITOR_SIN_QUOTES', error=err[:120])
            return                      # mark viejo > mark inventado

        # R6/R7 primero: cierran TODA la flota
        flota = salidas.evaluar_flota(self.cartera.pnl_dia_pct(),
                                      self.cartera.pnl_total_pct())
        if flota.cierra:
            self.diario.evento('HALT', motivo=flota.motivo, **flota.detalle)
            with self.lock_estado:
                self.cartera.halt = flota.motivo
                self.cartera.guardar()
            self.ejec.cancelar_abiertas(flota.motivo)
            self._cerrar_varias([(p, flota) for p in list(vivas)])
            return

        # R2 necesita RSI1m del subyacente DE CADA posicion. Reutilizar el de
        # la primera (p.ej. SPY) para una posicion QQQ daria una confirmacion
        # tecnicamente valida pero economicamente ajena.
        rsi_por_simbolo = {
            simbolo: self._rsi1m(simbolo, ahora)
            for simbolo in sorted({p.subyacente for p in vivas})
        }

        cierres = []
        for p in list(vivas):
            d = salidas.evaluar_posicion(
                p, minutos, ahora, rsi_por_simbolo.get(p.subyacente))
            if d.cierra:
                cierres.append((p, d))
        self._cerrar_varias(cierres)

    def _cerrar_varias(self, cierres):
        """Cierra posiciones independientes en paralelo.

        Cada escalera puede durar hasta 3 minutos. Con tres posiciones en
        serie, R5 tardaria hasta 9 minutos y no podria cumplir el flat. Los
        spreads tienen referencias/client_order_id distintos y Diario es
        thread-safe, por lo que paralelizarlos conserva idempotencia.
        """
        cierres = list(cierres)
        if not cierres:
            return
        if len(cierres) == 1:
            self._cerrar(*cierres[0])
            return
        hilos = [threading.Thread(target=self._cerrar, args=par, daemon=True)
                 for par in cierres]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

    def _rsi1m(self, simbolo, ahora):
        from motor import indicadores
        v1, err = datos.velas_1m(self.cli, simbolo, ahora - 6 * 3600, ahora)
        if err or len(v1) <= config.RSI_PERIODO:
            return None
        serie = indicadores.rsi_wilder([v.c for v in v1], config.RSI_PERIODO)
        return serie[-1]

    def _cerrar(self, pos, decision):
        quotes, err = contratos.cotizar(self.cli, [pos.sim_larga,
                                                   pos.sim_corta])
        if err:
            self.diario.evento('CIERRE_SIN_QUOTES', ref=pos.ref,
                               motivo=decision.motivo, error=err[:120])
            return
        spread = pos.spread_para_cierre(quotes)
        res, _ = self.ejec.cerrar(spread, pos.qty, self.cartera.sesion,
                                 pos.ref, decision.motivo, spread.mid)
        if res.get('filled'):
            with self.lock_estado:
                realizado = self.cartera.registrar_cierre(
                    pos, res.get('precio', spread.mid), decision.motivo,
                    res.get('qty', pos.qty))
            tipo = ('POSICION_CERRADA' if pos.cerrada
                    else 'POSICION_CERRADA_PARCIAL')
            self.diario.evento(tipo, ref=pos.ref,
                               motivo=decision.motivo,
                               detalle=decision.detalle,
                               realizado_usd=round(realizado, 2),
                               qty_cerrada=res.get('qty', pos.qty),
                               qty_restante=0 if pos.cerrada else pos.qty)
        elif res.get('sombra'):
            # Sombra observa y deja evidencia, pero nunca muta el estado de
            # una posicion que podria existir realmente en el broker.
            self.diario.evento('SOMBRA_POSICION_MANTENIDA', ref=pos.ref,
                               motivo=decision.motivo)

    # ------------------------------------------------------------------
    # LOOP
    # ------------------------------------------------------------------
    def _hilo_monitor(self):
        while not self.parar.is_set():
            try:
                self.ciclo_monitor()
            except Exception as e:                     # el monitor NO muere
                self.diario.evento('ERROR_MONITOR', error='%s: %s'
                                   % (type(e).__name__, e))
            self.parar.wait(config.MONITOR_S)

    def correr(self):
        problemas = self.cfg.validar()
        if problemas:
            for p in problemas:
                print('CONFIG: %s' % p)
            return 2

        lock = ejecutor.LockProceso(config.DIR_DIARIO / 'motor.lock')
        tomado, razon = lock.tomar()
        if not tomado:
            print('No arranco: %s' % razon)
            return 3
        try:
            self.cartera.cargar()
            error_cuenta = self.cartera.refrescar_cuenta()
            if error_cuenta:
                self.diario.evento('ARRANQUE_BLOQUEADO',
                                   razon='CUENTA_NO_VALIDADA',
                                   error=error_cuenta)
                print('No arranco: %s' % error_cuenta)
                return 4
            if self.cartera.vivas() and not self.cfg.opera_de_verdad:
                self.diario.evento(
                    'ARRANQUE_BLOQUEADO', razon='POSICIONES_EN_MODO_NO_REAL',
                    posiciones=[p.ref for p in self.cartera.vivas()])
                print('No arranco: hay posiciones persistidas y MODO no es real.')
                return 4
            informe = self.ejec.reconciliar(
                self.cartera.sesion, [p.ref for p in self.cartera.vivas()])
            if informe['bloqueos']:
                self.diario.evento('ARRANQUE_BLOQUEADO',
                                   razon='RECONCILIACION_AMBIGUA',
                                   bloqueos=informe['bloqueos'])
                print('No arranco: reconciliacion ambigua (%d bloqueo(s)).'
                      % len(informe['bloqueos']))
                return 4
            self.diario.evento('ARRANQUE', modo=self.cfg.modo,
                               simbolos=self.cfg.simbolos,
                               equity=self.cartera.equity,
                               posiciones=len(self.cartera.vivas()),
                               reconciliacion=informe)
            print('Remora Desk | MODO=%s | equity=$%.2f | posiciones=%d'
                  % (self.cfg.modo, self.cartera.equity,
                     len(self.cartera.vivas())))
            if informe['vivas']:
                print('AVISO: hay %d orden(es) viva(s) del arranque anterior.'
                      % len(informe['vivas']))

            hilo = threading.Thread(target=self._hilo_monitor, daemon=True)
            hilo.start()

            while not self.parar.is_set():
                objetivo = proximo_ciclo(time.time())
                espera = max(0.0, objetivo - time.time())
                if self.parar.wait(espera):
                    break
                abierto, err = self.mercado_abierto()
                if err:
                    self.diario.evento('API_DOWN', donde='clock',
                                       error=err[:120])
                    continue
                if not abierto:
                    continue
                for simbolo in self.cfg.simbolos:
                    try:
                        self.ciclo_entrada(simbolo)
                    except Exception as e:
                        self.diario.evento('ERROR_CICLO', simbolo=simbolo,
                                           error='%s: %s'
                                                 % (type(e).__name__, e))
        finally:
            self.parar.set()
            self.cartera.guardar()
            lock.soltar()
        return 0


def main(argv=None):
    from motor import rest
    ap = argparse.ArgumentParser(description='Remora Desk')
    ap.add_argument('--modo', choices=config.MODOS_VALIDOS,
                    help='sobrescribe MODO del .env (real solo a mano)')
    ap.add_argument('--un-ciclo', action='store_true',
                    help='corre un unico ciclo de entrada y termina')
    args = ap.parse_args(argv)

    cfg = config.Config()
    if args.modo:
        cfg.modo = args.modo
    if cfg.modo == 'real':
        print('*** MODO REAL: se mandaran ordenes a la cuenta paper ***')
    dia = diario.Diario(config.DIR_DIARIO)
    ag = Agente(cfg, rest.Cliente(cfg), dia)
    if args.un_ciclo:
        problemas = cfg.validar()
        if problemas:
            print('\n'.join('CONFIG: %s' % p for p in problemas))
            return 2
        # Tambien toma el lock: un ciclo suelto en MODO=real junto al loop
        # principal duplicaria ordenes, que es justo lo que el lock impide.
        lock = ejecutor.LockProceso(config.DIR_DIARIO / 'motor.lock')
        tomado, razon = lock.tomar()
        if not tomado:
            print('No arranco: %s' % razon)
            return 3
        try:
            ag.cartera.cargar()
            error_cuenta = ag.cartera.refrescar_cuenta()
            if error_cuenta:
                print('No arranco: %s' % error_cuenta)
                return 4
            if ag.cartera.vivas() and not cfg.opera_de_verdad:
                print('No arranco: hay posiciones persistidas y MODO no es real.')
                return 4
            informe = ag.ejec.reconciliar(
                ag.cartera.sesion, [p.ref for p in ag.cartera.vivas()])
            if informe['bloqueos']:
                print('No arranco: reconciliacion ambigua (%d bloqueo(s)).'
                      % len(informe['bloqueos']))
                return 4
            for simbolo in cfg.simbolos:
                reg = ag.ciclo_entrada(simbolo)
                print('%s -> %s (%s)' % (simbolo, reg.get('accion'),
                                         reg.get('razon', '')))
        finally:
            ag.cartera.guardar()
            lock.soltar()
        return 0
    return ag.correr()


if __name__ == '__main__':
    sys.exit(main())
