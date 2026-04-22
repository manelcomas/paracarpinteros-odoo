# -*- coding: utf-8 -*-
"""
Cliente SOAP para Web Service de Correos de Costa Rica (Pymexpress).

Documentación basada en el PDF oficial "Descripción de Interfaces del Web Service
de Correos de Costa Rica" versión 09/02/2023.

Endpoints (pruebas):
 - Token JSON: https://servicios.correos.go.cr:442/Token/authenticate
 - SOAP:       http://amistad.correos.go.cr:84/wsAppCorreos.wsAppCorreos.svc
 - Rastreo:    https://servicios.correos.go.cr/rastreoQA/consulta_envios/rastreo.aspx

El token tiene vencimiento de 5 minutos → lo cacheamos en memoria por (user, sistema).
"""

import logging
import time
from datetime import datetime

import requests
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from zeep import Client, Settings
    from zeep.transports import Transport
    from zeep.exceptions import Fault as ZeepFault
except ImportError:
    _logger.warning("El paquete 'zeep' no está instalado. Ejecutar: pip install zeep")
    Client = None

# Códigos de respuesta del WS
RESP_OK = '00'
RESP_ERROR_INTERNO = '15'
RESP_ERROR_VALIDACION = '17'
RESP_TOKEN_INVALIDO = '20'

# Cache de tokens en memoria del proceso (clave: hash de credenciales)
_TOKEN_CACHE = {}
_TOKEN_TTL_SECONDS = 4 * 60  # margen de 1 min sobre los 5 min reales


class CorreosCRClient:
    """
    Cliente ligero que agrupa las llamadas al WS.
    Instanciar con los valores de configuración (ambiente pruebas o producción).
    """

    def __init__(self, username, password, sistema, user_id, servicio_id,
                 codigo_cliente, token_url, soap_url, timeout=30):
        if not Client:
            raise UserError(_(
                "Falta el paquete Python 'zeep'. Añádelo a requirements.txt "
                "y reinstala el módulo en Odoo.sh."
            ))
        self.username = username
        self.password = password
        self.sistema = sistema  # "PYMEXPRESS"
        self.user_id = user_id  # ej: 304410837
        self.servicio_id = servicio_id  # ej: 73
        self.codigo_cliente = codigo_cliente  # ej: 265304
        self.token_url = token_url
        self.soap_url = soap_url
        self.timeout = timeout
        self._soap_client = None

    # ───────────────────── TOKEN ─────────────────────

    def _cache_key(self):
        return f"{self.username}::{self.sistema}::{self.token_url}"

    def get_token(self):
        """Devuelve un token válido. Usa cache en memoria con TTL < 5 min."""
        key = self._cache_key()
        cached = _TOKEN_CACHE.get(key)
        now = time.time()
        if cached and cached['expires_at'] > now:
            return cached['token']

        _logger.info("Correos CR: solicitando nuevo token para %s", self.username)
        try:
            resp = requests.post(
                self.token_url,
                json={
                    'Username': self.username,
                    'Password': self.password,
                    'Sistema': self.sistema,
                },
                timeout=self.timeout,
                verify=True,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise UserError(_(
                "Error conectando con Correos CR (autenticación): %s"
            ) % str(e))
        except ValueError:
            raise UserError(_(
                "Respuesta inválida del servicio de autenticación de Correos CR."
            ))

        # El WS devuelve normalmente {"Token": "..."} — tolerar variantes
        token = data.get('Token') or data.get('token') or data.get('access_token')
        if not token:
            raise UserError(_(
                "No se pudo obtener token de Correos CR. Respuesta: %s"
            ) % str(data)[:300])

        _TOKEN_CACHE[key] = {
            'token': token,
            'expires_at': now + _TOKEN_TTL_SECONDS,
        }
        return token

    def invalidate_token(self):
        _TOKEN_CACHE.pop(self._cache_key(), None)

    # ───────────────────── SOAP CLIENT ─────────────────────

    def _get_soap_client(self):
        if self._soap_client is None:
            wsdl = self.soap_url + '?wsdl' if not self.soap_url.endswith('?wsdl') else self.soap_url
            transport = Transport(timeout=self.timeout, operation_timeout=self.timeout)
            settings = Settings(strict=False, xml_huge_tree=True)
            self._soap_client = Client(wsdl=wsdl, transport=transport, settings=settings)
        return self._soap_client

    def _call(self, method_name, **kwargs):
        """
        Invoca un método SOAP con el token en el primer parámetro (estándar del WS).
        Reintenta una vez si recibe RESP_TOKEN_INVALIDO.
        """
        client = self._get_soap_client()
        for attempt in (1, 2):
            token = self.get_token()
            try:
                method = getattr(client.service, method_name)
                result = method(token=token, **kwargs)
            except ZeepFault as e:
                raise UserError(_(
                    "Error SOAP en %(method)s: %(msg)s",
                    method=method_name, msg=str(e)
                ))
            except Exception as e:
                raise UserError(_(
                    "Error de comunicación con Correos CR (%(method)s): %(msg)s",
                    method=method_name, msg=str(e)
                ))

            cod = getattr(result, 'CodRespuesta', None)
            if cod == RESP_TOKEN_INVALIDO and attempt == 1:
                self.invalidate_token()
                continue
            return result

        return result  # fallback

    # ───────────────────── MÉTODOS GEOGRÁFICOS ─────────────────────

    def get_provincias(self):
        r = self._call('ccrCodProvincia')
        self._check_response(r, 'ccrCodProvincia')
        return [(i.Codigo, i.Descripcion) for i in (r.Provincias or [])]

    def get_cantones(self, cod_provincia):
        r = self._call('ccrCodCanton', CodProvincia=str(cod_provincia))
        self._check_response(r, 'ccrCodCanton')
        return [(i.Codigo, i.Descripcion) for i in (r.Cantones or [])]

    def get_distritos(self, cod_provincia, cod_canton):
        r = self._call('ccrCodDistrito',
                       CodProvincia=str(cod_provincia),
                       CodCanton=str(cod_canton))
        self._check_response(r, 'ccrCodDistrito')
        return [(i.Codigo, i.Descripcion) for i in (r.Distritos or [])]

    def get_codigo_postal(self, cod_provincia, cod_canton, cod_distrito):
        r = self._call('ccrCodPostal',
                       CodProvincia=str(cod_provincia),
                       CodCanton=str(cod_canton),
                       CodDistrito=str(cod_distrito))
        self._check_response(r, 'ccrCodPostal')
        return r.CodPostal

    # ───────────────────── TARIFA ─────────────────────

    def get_tarifa(self, prov_origen, canton_origen, prov_destino, canton_destino, peso_gramos):
        """Devuelve dict con MontoTarifa, Descuento, Impuesto."""
        req = {
            'ProvinciaOrigen': str(prov_origen),
            'CantonOrigen': str(canton_origen),
            'ProvinciaDestino': str(prov_destino),
            'CantonDestino': str(canton_destino),
            'Servicio': str(self.servicio_id),
            'Peso': peso_gramos,
        }
        r = self._call('ccrTarifa', reqTarifa=req)
        self._check_response(r, 'ccrTarifa')
        return {
            'monto': r.MontoTarifa,
            'descuento': r.Descuento,
            'impuesto': r.Impuesto,
        }

    # ───────────────────── GUÍA + ENVÍO ─────────────────────

    def generar_guia(self):
        """Genera el número de guía (ENVIO_ID). Paso 1 del flujo."""
        r = self._call('ccrGenerarGuia')
        self._check_response(r, 'ccrGenerarGuia')
        return r.NumeroEnvio

    def registrar_envio(self, envio_id, envio_data):
        """
        Paso 2 del flujo. envio_data es dict con claves de ccrDatosEnvio.
        Devuelve tuple (codigo_respuesta, mensaje, pdf_base64).
        """
        # Construcción del payload siguiendo ccrDatosEnvio del PDF.
        now_ms = int(time.time() * 1000)
        payload = {
            'COD_CLIENTE': str(self.codigo_cliente),
            'FECHA_ENVIO': envio_data.get('fecha_envio') or datetime.now(),
            'ENVIO_ID': envio_id,
            'SERVICIO': str(self.servicio_id),
            'MONTO_FLETE': envio_data.get('monto_flete', 0),
            'DEST_NOMBRE': envio_data['dest_nombre'][:200],
            'DEST_DIRECCION': envio_data['dest_direccion'][:500],
            'DEST_TELEFONO': envio_data.get('dest_telefono', '')[:15],
            'DEST_APARTADO': envio_data['dest_zip'][:20],  # requerido
            'DEST_ZIP': envio_data['dest_zip'][:8],
            'SEND_NOMBRE': envio_data['send_nombre'][:200],
            'SEND_DIRECCION': envio_data['send_direccion'][:500],
            'SEND_ZIP': envio_data['send_zip'][:8],
            'SEND_TELEFONO': envio_data.get('send_telefono', '')[:15],
            'OBSERVACIONES': envio_data.get('observaciones', '')[:200],
            'USUARIO_ID': int(self.user_id),
            'PESO': envio_data['peso'],
            'VARIABLE_1': '0', 'VARIABLE_3': '0', 'VARIABLE_4': '0',
            'VARIABLE_5': 0, 'VARIABLE_6': '0', 'VARIABLE_7': '0',
            'VARIABLE_8': '0', 'VARIABLE_9': '0', 'VARIABLE_10': '0',
            'VARIABLE_11': '0', 'VARIABLE_12': 0,
            'VARIABLE_13': '0', 'VARIABLE_14': '0',
            'VARIABLE_15': '0', 'VARIABLE_16': '0',
        }

        req = {
            'Cliente': str(self.codigo_cliente),
            'Envio': payload,
        }
        r = self._call('ccrRegistroEnvio', ccrReqEnvio=req)
        cod = getattr(r, 'CodRespuesta', None)
        msg = getattr(r, 'MensajeRespuesta', '')
        pdf = getattr(r, 'PDF', None)
        if cod != RESP_OK:
            raise UserError(_(
                "Correos CR rechazó el envío (%(cod)s): %(msg)s",
                cod=cod, msg=msg
            ))
        return cod, msg, pdf

    # ───────────────────── TRACKING ─────────────────────

    def tracking(self, numero_envio):
        r = self._call('ccrMovilTracking', NumeroEnvio=numero_envio)
        self._check_response(r, 'ccrMovilTracking')
        encabezado = getattr(r, 'Encabezado', None)
        eventos = getattr(r, 'Eventos', []) or []
        return {
            'encabezado': {
                'numero': encabezado.NumeroEnvio if encabezado else '',
                'fecha_recepcion': encabezado.FechaRecepcion if encabezado else '',
                'destinatario': encabezado.NombreDestinatario if encabezado else '',
                'estado': encabezado.Estado if encabezado else '',
                'referencia': encabezado.Referencia if encabezado else '',
            } if encabezado else {},
            'eventos': [{
                'fecha': e.FechaHora,
                'unidad': e.Unidad,
                'evento': e.Evento,
                'recibido_por': getattr(e, 'RecibidoPor', ''),
            } for e in eventos],
        }

    # ───────────────────── HELPERS ─────────────────────

    @staticmethod
    def _check_response(r, method):
        cod = getattr(r, 'CodRespuesta', None)
        if cod != RESP_OK:
            msg = getattr(r, 'MensajeRespuesta', '')
            raise UserError(_(
                "Error en %(method)s: [%(cod)s] %(msg)s",
                method=method, cod=cod, msg=msg
            ))
