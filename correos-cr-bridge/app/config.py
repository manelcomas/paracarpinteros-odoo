# -*- coding: utf-8 -*-
"""Configuración centralizada cargada desde .env"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    # Correos CR
    correos_env: str = 'test'
    correos_username: str
    correos_password: str
    correos_sistema: str = 'PYMEXPRESS'
    correos_user_id: str
    correos_servicio_id: str = '73'
    correos_codigo_cliente: str
    correos_token_url: str
    correos_soap_url: str

    # Odoo
    odoo_url: str
    odoo_db: str
    odoo_username: str
    odoo_api_key: str

    # Remitente
    sender_name: str
    sender_address: str
    sender_zip: str
    sender_phone: str
    sender_provincia_code: str = '3'
    sender_canton_code: str = '05'
    sender_distrito_code: str = '04'

    # Worker
    poll_interval_minutes: int = 5
    default_weight_g: int = 500

    # API
    api_token: str


settings = Settings()
