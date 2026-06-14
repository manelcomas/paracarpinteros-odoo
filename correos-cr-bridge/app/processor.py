# -*- coding: utf-8 -*-
"""
Orquestador: detecta pickings pendientes en Odoo, llama a Correos CR,
y adjunta el PDF de la etiqueta al picking.
"""

import base64
import logging
import re
import threading
from datetime import datetime

from .config import settings
from .correos_client import CorreosCRClient, CorreosCRError
from .odoo_client import OdooClient, OdooError

_logger = logging.getLogger(__name__)

# Lock por picking_id: evita que el worker automático y /process-now generen
# dos guías para el mismo picking. Es proceso-local (workers=1 en Docker).
_in_flight: set[int] = set()
_in_flight_lock = threading.Lock()


def _m2o_name(value):
    """Devuelve el 'name' de un campo Many2one de Odoo ([id, name]) o ''."""
    if value and isinstance(value, (list, tuple)) and len(value) > 1:
        return (value[1] or '').strip()
    return ''


# ZIP CR oficial = PCCDD; el primer dígito es la provincia.
PROVINCIAS_CR = {
    '1': 'San José', '2': 'Alajuela', '3': 'Cartago', '4': 'Heredia',
    '5': 'Guanacaste', '6': 'Puntarenas', '7': 'Limón',
}

# Placeholders que el modal pone en cantón/distrito cuando el partner no tiene
# el dato ("Cantón 03", "Distrito 05"): no son nombres reales, se descartan.
_GEO_PLACEHOLDER = re.compile(r'^(?:Cantón|Distrito)\s*\d+$', re.IGNORECASE)


def _zip_digits(value) -> str:
    """Normaliza a un ZIP CR de 5 dígitos válido (PCCDD) o '' si no lo es."""
    z = ''.join(c for c in (value or '') if c.isdigit())
    return z if len(z) == 5 and z[0] in '1234567' else ''


def _clean_geo(value: str) -> str:
    """Limpia un nombre de cantón/distrito venido del modal: descarta los
    placeholders 'Cantón NN'/'Distrito NN' que no aportan info al cartero."""
    v = (value or '').strip()
    return '' if _GEO_PLACEHOLDER.match(v) else v


def build_dest_direccion(
    partner: dict,
    senas_override: str = '',
    zip_override: str = '',
    canton_override: str = '',
    distrito_override: str = '',
    provincia_override: str = '',
    zip_map: dict | None = None,
) -> str:
    """
    Construye el DEST_DIRECCION que se envía a Correos CR concatenando
    señas + distrito + cantón + provincia, separados por coma.

    Correos imprime literalmente este campo en la etiqueta como "Dirección",
    así que para que aparezcan provincia/cantón/distrito en el PDF hay que
    incluirlos aquí (el WS no tiene campos separados para ellos).

    - senas_override: texto que ya viene del modal del panel (lo que el
      usuario editó). Si está vacío, cae a x_studio_senas y luego a street.
    - Geografía (Distrito/Cantón/Provincia) por prioridad **ZIP → modal →
      partner**, para no mezclar datos cuando el operador corrige a mano:
        1. Si el ZIP efectivo (zip_override del modal o el del partner) es un
           CR válido y está en zip_map, se derivan distrito/cantón de la tabla
           maestra y la provincia del primer dígito. El ZIP manda: nunca queda
           pegada la geo vieja del partner a unas señas nuevas.
        2. Si no, los valores que el operador dejó en el modal.
        3. Si no, los Studio fields del partner.
    - El nombre de la provincia que devuelve Odoo viene con sufijo
      " (CR)" — se limpia.
    - Se trunca a 500 chars (límite del WS).
    """
    senas = (senas_override or '').strip()
    if not senas:
        senas = (partner.get('x_studio_senas') or partner.get('street') or '').strip()

    canton = distrito = provincia = ''

    def _from_zip(z):
        """Rellena los huecos de canton/distrito/provincia desde un ZIP del mapa."""
        nonlocal canton, distrito, provincia
        entry = zip_map.get(z) if (z and zip_map) else None
        if entry:
            # zip_map[z] = (dist_id, dist_name, cant_id, cant_name)
            distrito = distrito or (entry[1] or '').strip()
            canton = canton or (entry[3] or '').strip()
            provincia = provincia or PROVINCIAS_CR.get(z[0], '')

    # 1) ZIP que el operador editó en el modal: fuente de la verdad.
    _from_zip(_zip_digits(zip_override))

    # 2) Lo que el operador dejó escrito en el modal.
    if not canton:
        canton = _clean_geo(canton_override)
    if not distrito:
        distrito = _clean_geo(distrito_override)
    if not provincia:
        provincia = (provincia_override or '').replace(' (CR)', '').strip()

    # 3) Partner: primero su propio ZIP (clave para el worker automático, que
    #    no pasa por el modal), luego los Studio fields como último recurso.
    _from_zip(_zip_digits(partner.get('zip', '')))
    if not canton:
        canton = _m2o_name(partner.get('x_studio_canton_cr'))
    if not distrito:
        distrito = _m2o_name(partner.get('x_studio_distrito_cr'))
    if not provincia:
        provincia = _m2o_name(partner.get('state_id')).replace(' (CR)', '').strip()

    parts = [senas]
    if distrito:
        parts.append(f'Distrito {distrito}')
    if canton:
        parts.append(f'Cantón {canton}')
    if provincia:
        parts.append(provincia)
    return ', '.join(p for p in parts if p)[:500]


class Processor:
    def __init__(self):
        self.correos = CorreosCRClient(
            username=settings.correos_username,
            password=settings.correos_password,
            sistema=settings.correos_sistema,
            user_id=settings.correos_user_id,
            servicio_id=settings.correos_servicio_id,
            codigo_cliente=settings.correos_codigo_cliente,
            token_url=settings.correos_token_url,
            soap_url=settings.correos_soap_url,
        )
        self.odoo = OdooClient(
            url=settings.odoo_url,
            db=settings.odoo_db,
            username=settings.odoo_username,
            api_key=settings.odoo_api_key,
        )

    def run_once(self) -> dict:
        """Una pasada: procesa todos los pickings pendientes."""
        start = datetime.now()
        stats = {'checked': 0, 'processed': 0, 'errors': 0, 'errors_detail': []}

        try:
            pickings = self.odoo.search_pickings_pendientes(limit=20)
        except OdooError as e:
            _logger.error("No pude leer pickings de Odoo: %s", e)
            stats['errors'] += 1
            stats['errors_detail'].append(f"Odoo read: {e}")
            return stats

        stats['checked'] = len(pickings)
        _logger.info("Pickings pendientes encontrados: %d", len(pickings))

        for pk in pickings:
            try:
                self._process_one(pk)
                stats['processed'] += 1
            except Exception as e:
                stats['errors'] += 1
                stats['errors_detail'].append(f"Picking {pk.get('name')}: {e}")
                _logger.exception("Error procesando picking %s", pk.get('name'))
                # Publicar el error en el chatter del picking
                try:
                    self.odoo.post_message(
                        pk['id'],
                        f"<b>⚠ Error generando guía Correos CR:</b><br/>{e}"
                    )
                except Exception:
                    pass

        stats['duration_s'] = (datetime.now() - start).total_seconds()
        return stats

    def _process_one(self, picking: dict):
        pk_id = picking['id']
        pk_name = picking['name']

        # Lock por picking_id: si otro hilo/worker ya lo está procesando, salir.
        with _in_flight_lock:
            if pk_id in _in_flight:
                _logger.info("Picking %s ya está siendo procesado por otro flujo, salto", pk_name)
                return
            _in_flight.add(pk_id)

        try:
            self._process_one_locked(picking, pk_id, pk_name)
        finally:
            with _in_flight_lock:
                _in_flight.discard(pk_id)

    def _process_one_locked(self, picking: dict, pk_id: int, pk_name: str):
        _logger.info("Procesando picking %s (id=%d)", pk_name, pk_id)

        # Recheck en Odoo justo antes de generar la guía: si ya tiene tracking,
        # otro proceso o el módulo Odoo nativo se adelantó.
        check = self.odoo.execute_kw('stock.picking', 'read', [[pk_id]],
                                     {'fields': ['carrier_tracking_ref']})
        if check and check[0].get('carrier_tracking_ref'):
            _logger.info("Picking %s ya tiene tracking %s, salto",
                         pk_name, check[0]['carrier_tracking_ref'])
            return

        # 1) Leer partner destino
        partner_id = picking['partner_id'][0] if picking.get('partner_id') else None
        if not partner_id:
            raise Exception("Picking sin cliente asignado")
        partner = self.odoo.read_partner(partner_id)

        # Validaciones mínimas
        if not partner.get('zip'):
            raise Exception(f"Cliente '{partner.get('name')}' sin código postal")
        direccion_dest = build_dest_direccion(partner, zip_map=self.odoo._load_zip_map())
        if not direccion_dest:
            raise Exception(f"Cliente '{partner.get('name')}' sin dirección")

        # 2) Calcular peso real
        peso_g = self._calc_peso(pk_id)

        # 3) Llamar al WS
        envio_id = self.correos.generar_guia()
        _logger.info("Guía generada: %s", envio_id)

        envio_data = {
            'fecha_envio': datetime.now(),
            'monto_flete': 0,
            'dest_nombre': partner.get('name', ''),
            'dest_direccion': direccion_dest,
            'dest_telefono': (partner.get('phone') or '').replace(' ', '').replace('-', ''),
            'dest_zip': partner.get('zip', ''),
            'send_nombre': settings.sender_name,
            'send_direccion': settings.sender_address,
            'send_zip': settings.sender_zip,
            'send_telefono': settings.sender_phone.replace(' ', '').replace('-', ''),
            'observaciones': 'Herramientas',
            'peso': peso_g,
        }
        cod, msg, pdf_b64 = self.correos.registrar_envio(envio_id, envio_data)

        # 4) Adjuntar PDF al picking
        if pdf_b64:
            pdf_str = pdf_b64 if isinstance(pdf_b64, str) else base64.b64encode(pdf_b64).decode()
            att_id = self.odoo.attach_pdf(
                pk_id,
                f"Etiqueta_CorreosCR_{envio_id}.pdf",
                pdf_str,
            )
            self.odoo.post_message(
                pk_id,
                f"✅ <b>Guía Correos CR generada</b><br/>"
                f"Número: <b>{envio_id}</b><br/>"
                f"Peso: {peso_g} g<br/>"
                f"Rastreo: <a href=\"https://correos.go.cr/rastreo?tracking={envio_id}\" target=\"_blank\">Ver en Correos CR</a>",
                attachment_ids=[att_id],
            )

        # 5) Guardar tracking
        self.odoo.set_tracking(pk_id, envio_id)
        _logger.info("Picking %s actualizado con tracking %s", pk_name, envio_id)

    def _calc_peso(self, pk_id: int) -> int:
        moves = self.odoo.read_picking_moves(pk_id)
        if not moves:
            return settings.default_weight_g
        product_ids = list({m['product_id'][0] for m in moves if m.get('product_id')})
        weights = self.odoo.read_product_weights(product_ids)

        total_g = 0.0
        sin_peso = []
        for m in moves:
            pid = m['product_id'][0] if m.get('product_id') else None
            qty = m.get('product_uom_qty') or 0
            w_kg = weights.get(pid, 0) or 0
            if w_kg > 0:
                total_g += w_kg * 1000 * qty
            else:
                # Sin peso en Odoo: se declara el default por unidad (sobre-declara,
                # que es el lado seguro para Correos). Se avisa para corregir el dato.
                total_g += settings.default_weight_g * qty
                if m.get('product_id'):
                    sin_peso.append(f"{m['product_id'][1]} (x{qty:g})")
        if sin_peso:
            _logger.warning(
                "Picking %s: %d producto(s) sin peso en Odoo, declarados a %d g/u por "
                "defecto (puede inflar la tarifa Pymexpress): %s. Asigná el peso real en el producto.",
                pk_id, len(sin_peso), settings.default_weight_g, "; ".join(sin_peso))
        return max(int(total_g), settings.default_weight_g)
