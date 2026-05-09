# -*- coding: utf-8 -*-
"""
Cliente SOAP del Web Service de Correos de Costa Rica (Pymexpress).
Adaptado del módulo Odoo a microservicio independiente.
"""

import logging
import time
from datetime import datetime
from typing import Optional

import requests
from zeep import Client, Settings as ZeepSettings
from zeep.transports import Transport
from zeep.exceptions import Fault as ZeepFault

_logger = logging.getLogger(__name__)

RESP_OK = '00'
RESP_TOKEN_INVALIDO = '20'
_TOKEN_TTL_SECONDS = 4 * 60  # margen sobre los 5 min reales


class CorreosCRError(Exception):
    """Error específico de Correos CR (WS rechazó, credenciales mal, etc.)"""


class CorreosCRClient:
    def __init__(self, username, password, sistema, user_id, servicio_id,
                 codigo_cliente, token_url, soap_url, timeout=60):
        self.username = username
        self.password = password
        self.sistema = sistema
        self.user_id = user_id
        self.servicio_id = servicio_id
        self.codigo_cliente = codigo_cliente
        self.token_url = token_url
        self.soap_url = soap_url
        self.timeout = timeout
        self._soap_client: Optional[Client] = None
        self._token: Optional[str] = None
        self._token_expires_at: float = 0

    # ───────────── TOKEN ─────────────
    def get_token(self) -> str:
        now = time.time()
        if self._token and self._token_expires_at > now:
            return self._token
        try:
            resp = requests.post(
                self.token_url,
                json={
                    'Username': self.username,
                    'Password': self.password,
                    'Sistema': self.sistema,
                },
                timeout=self.timeout,
                headers={'Content-Type': 'application/json'},
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise CorreosCRError(f"Error autenticando con Correos CR: {e}")

        body = resp.text.strip()
        if not body:
            raise CorreosCRError("Respuesta vacia del endpoint de token")
        try:
            import json as _json
            data = _json.loads(body)
            if isinstance(data, dict):
                token = data.get('Token') or data.get('token') or data.get('access_token') or ''
            else:
                token = str(data)
        except (ValueError, TypeError):
            token = body
        if token.lower().startswith('bearer '):
            token = token[7:].strip()
        token = token.strip('"').strip()
        if not token:
            raise CorreosCRError(f"Token vacio tras parseo: {body[:200]}")

        self._token = token
        self._token_expires_at = now + _TOKEN_TTL_SECONDS
        _logger.info("Token Correos CR renovado (válido %ds)", _TOKEN_TTL_SECONDS)
        return token

    def _invalidate_token(self):
        self._token = None
        self._token_expires_at = 0

    # ───────────── SOAP ─────────────
    def _get_client(self) -> Client:
        if self._soap_client is None:
            wsdl = self.soap_url if self.soap_url.endswith('?wsdl') else self.soap_url + '?wsdl'
            transport = Transport(timeout=self.timeout, operation_timeout=self.timeout)
            settings = ZeepSettings(strict=False, xml_huge_tree=True)
            self._soap_client = Client(wsdl=wsdl, transport=transport, settings=settings)
        return self._soap_client

    def _call(self, method_name, **kwargs):
        client = self._get_client()
        for attempt in (1, 2):
            token = self.get_token()
            # Token va como header HTTP Authorization, no como parametro SOAP
            client.transport.session.headers.update({'Authorization': f'Bearer {token}'})
            try:
                method = getattr(client.service, method_name)
                result = method(**kwargs)
            except ZeepFault as e:
                raise CorreosCRError(f"SOAP fault en {method_name}: {e}")
            except Exception as e:
                raise CorreosCRError(f"Error comunicacion {method_name}: {e}")

            cod = getattr(result, 'CodRespuesta', None)
            if cod == RESP_TOKEN_INVALIDO and attempt == 1:
                self._invalidate_token()
                continue
            return result
        return result

    def _check(self, r, method):
        cod = getattr(r, 'CodRespuesta', None)
        if cod != RESP_OK:
            msg = getattr(r, 'MensajeRespuesta', '')
            raise CorreosCRError(f"{method} falló [{cod}]: {msg}")

    # ───────────── Guía + Envío ─────────────
    def generar_guia(self) -> str:
        r = self._call('ccrGenerarGuia')
        self._check(r, 'ccrGenerarGuia')
        return r.NumeroEnvio

    def registrar_envio(self, envio_id: str, envio_data: dict):
        """
        Devuelve tuple (cod_respuesta, mensaje, pdf_base64_str).
        envio_data debe traer: fecha_envio, dest_nombre, dest_direccion,
        dest_telefono, dest_zip, send_nombre, send_direccion, send_zip,
        send_telefono, observaciones, peso (gramos, int).
        """
        payload = {
            'COD_CLIENTE': str(self.codigo_cliente),
            'FECHA_ENVIO': envio_data.get('fecha_envio') or datetime.now(),
            'ENVIO_ID': envio_id,
            'SERVICIO': str(self.servicio_id),
            'MONTO_FLETE': envio_data.get('monto_flete', 0),
            'DEST_NOMBRE': (envio_data['dest_nombre'] or '')[:200],
            'DEST_DIRECCION': (envio_data['dest_direccion'] or '')[:500],
            'DEST_TELEFONO': (envio_data.get('dest_telefono', '') or '')[:15],
            'DEST_APARTADO': (envio_data['dest_zip'] or '')[:20],
            'DEST_ZIP': (envio_data['dest_zip'] or '')[:8],
            'SEND_NOMBRE': (envio_data['send_nombre'] or '')[:200],
            'SEND_DIRECCION': (envio_data['send_direccion'] or '')[:500],
            'SEND_ZIP': (envio_data['send_zip'] or '')[:8],
            'SEND_TELEFONO': (envio_data.get('send_telefono', '') or '')[:15],
            'OBSERVACIONES': (envio_data.get('observaciones', '') or '')[:200],
            'USUARIO_ID': int(self.user_id),
            'PESO': int(envio_data['peso']),
            'VARIABLE_1': '0', 'VARIABLE_3': '0', 'VARIABLE_4': '0',
            'VARIABLE_5': 0, 'VARIABLE_6': '0', 'VARIABLE_7': '0',
            'VARIABLE_8': '0', 'VARIABLE_9': '0', 'VARIABLE_10': '0',
            'VARIABLE_11': '0', 'VARIABLE_12': 0,
            'VARIABLE_13': '0', 'VARIABLE_14': '0',
            'VARIABLE_15': '0', 'VARIABLE_16': '0',
        }
        req = {'Cliente': str(self.codigo_cliente), 'Envio': payload}
        r = self._call('ccrRegistroEnvio', ccrReqEnvio=req)
        cod = getattr(r, 'CodRespuesta', None)
        msg = getattr(r, 'MensajeRespuesta', '')
        pdf = getattr(r, 'PDF', None)
        if cod != RESP_OK:
            raise CorreosCRError(f"ccrRegistroEnvio rechazó [{cod}]: {msg}")
        return cod, msg, pdf

    def tracking(self, numero_envio: str) -> dict:
        r = self._call('ccrMovilTracking', NumeroEnvio=numero_envio)
        self._check(r, 'ccrMovilTracking')
        enc = getattr(r, 'Encabezado', None)
        eventos = getattr(r, 'Eventos', []) or []
        return {
            'encabezado': {
                'numero': enc.NumeroEnvio if enc else '',
                'fecha_recepcion': str(enc.FechaRecepcion) if enc else '',
                'destinatario': enc.NombreDestinatario if enc else '',
                'estado': enc.Estado if enc else '',
                'referencia': enc.Referencia if enc else '',
            } if enc else {},
            'eventos': [{
                'fecha': str(e.FechaHora),
                'unidad': e.Unidad,
                'evento': e.Evento,
                'recibido_por': getattr(e, 'RecibidoPor', ''),
            } for e in eventos],
        }

    # ───────────── Geo ─────────────
    def get_cantones(self, cod_provincia):
        r = self._call('ccrCodCanton', CodProvincia=str(cod_provincia))
        self._check(r, 'ccrCodCanton')
        out = []
        _w = getattr(r, 'Cantones', None); items = getattr(_w, 'ccrItemGeografico', None) if _w else None
        for i in (items or []):
            cod = getattr(i, 'Codigo', None)
            desc = getattr(i, 'Descripcion', None) or getattr(i, 'Nombre', None)
            if cod and desc:
                out.append((cod, desc))
        return out

    def get_distritos(self, cod_provincia, cod_canton):
        r = self._call('ccrCodDistrito', CodProvincia=str(cod_provincia), CodCanton=str(cod_canton))
        self._check(r, 'ccrCodDistrito')
        out = []
        _w = getattr(r, 'Distritos', None); items = getattr(_w, 'ccrItemGeografico', None) if _w else None
        for i in (items or []):
            cod = getattr(i, 'Codigo', None)
            desc = getattr(i, 'Descripcion', None) or getattr(i, 'Nombre', None)
            if cod and desc:
                out.append((cod, desc))
        return out

    def get_provincias(self):
        r = self._call('ccrCodProvincia')
        self._check(r, 'ccrCodProvincia')
        out = []
        _w = getattr(r, 'Provincias', None); items = getattr(_w, 'ccrItemGeografico', None) if _w else None
        for i in (items or []):
            cod = getattr(i, 'Codigo', None) or getattr(i, 'codigo', None)
            desc = getattr(i, 'Descripcion', None) or getattr(i, 'descripcion', None) or getattr(i, 'Nombre', None)
            if cod and desc:
                out.append((cod, desc))
        return out

    def get_codigo_postal(self, prov, canton, distrito) -> str:
        r = self._call('ccrCodPostal',
                       CodProvincia=str(prov),
                       CodCanton=str(canton),
                       CodDistrito=str(distrito))
        self._check(r, 'ccrCodPostal')
        return r.CodPostal
