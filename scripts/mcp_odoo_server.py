#!/usr/bin/env python3
"""Servidor MCP de Odoo (XML-RPC) para el proyecto Paracarpinteros.

Expone la instancia Odoo Online al asistente sin escribir un script suelto por
cada consulta. Lee las credenciales del baúl `.env` raíz vía `scripts/_env.py`
(ODOO_URL / ODOO_DB / ODOO_USERNAME / ODOO_API_KEY).

Registrado en `.mcp.json` (project-scoped) para que Claude Code lo arranque solo.

Herramientas:
  - odoo_search_read   consultar registros (model, domain, fields, limit, order)
  - odoo_read          leer por ids
  - odoo_fields_get    inspeccionar el esquema de un modelo (¡úsalo ANTES de
                       escribir campos nuevos! — la geo CR usa Studio fields)
  - odoo_search_count  contar registros de un dominio
  - odoo_name_search   buscar por nombre (devuelve [id, display_name])
  - odoo_create        crear un registro (mutación)
  - odoo_write         escribir valores en ids (mutación)
  - odoo_execute       escape hatch: execute_kw genérico (method + args + kwargs)
  - odoo_unlink        borrar ids (gated: ODOO_MCP_ALLOW_UNLINK=1)

Salvaguardas:
  - Las mutaciones (create/write/execute con method de escritura/unlink) se
    desactivan si ODOO_MCP_READONLY=1.
  - odoo_unlink exige ODOO_MCP_ALLOW_UNLINK=1 aparte (borrar es irreversible).

Self-test (no arranca MCP, solo prueba la conexión):
    .venv/bin/python scripts/mcp_odoo_server.py --selftest
"""
from __future__ import annotations

import json
import os
import sys
import xmlrpc.client
from pathlib import Path
from typing import Any

# --- baúl .env raíz ---------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import load_project_env  # noqa: E402

load_project_env()

ODOO_URL = os.environ.get("ODOO_URL", "").rstrip("/")
ODOO_DB = os.environ.get("ODOO_DB", "")
ODOO_USER = os.environ.get("ODOO_USERNAME", "")
ODOO_KEY = os.environ.get("ODOO_API_KEY", "")

READONLY = os.environ.get("ODOO_MCP_READONLY", "0") == "1"
ALLOW_UNLINK = os.environ.get("ODOO_MCP_ALLOW_UNLINK", "0") == "1"

# métodos de Odoo considerados de escritura (para el guard de odoo_execute)
_WRITE_METHODS = {
    "create", "write", "unlink", "copy",
    "action_confirm", "button_confirm", "action_post", "action_cancel",
}

# --- conexión perezosa con uid cacheado -------------------------------------
_uid: int | None = None
_common: xmlrpc.client.ServerProxy | None = None
_object: xmlrpc.client.ServerProxy | None = None


def _connect() -> tuple[int, xmlrpc.client.ServerProxy]:
    """Autentica (una vez) y devuelve (uid, proxy de object)."""
    global _uid, _common, _object
    if not all((ODOO_URL, ODOO_DB, ODOO_USER, ODOO_KEY)):
        raise RuntimeError(
            "Faltan credenciales Odoo en el baúl .env "
            "(ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)."
        )
    if _uid is None:
        _common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
        uid = _common.authenticate(ODOO_DB, ODOO_USER, ODOO_KEY, {})
        if not uid:
            # authenticate devuelve False (no excepción) si la key expiró/es inválida
            raise RuntimeError(
                "authenticate devolvió False: API key Odoo inválida o EXPIRADA. "
                "Ver 'API key con expiración' en CLAUDE.md (rotar la del baúl)."
            )
        _uid = uid
        _object = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    return _uid, _object  # type: ignore[return-value]


def _call(model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
    uid, obj = _connect()
    return obj.execute_kw(ODOO_DB, uid, ODOO_KEY, model, method, args, kwargs or {})


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _guard_write() -> None:
    if READONLY:
        raise RuntimeError("ODOO_MCP_READONLY=1 — mutaciones desactivadas.")


# --- servidor MCP -----------------------------------------------------------
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("odoo-paracarpinteros")


@mcp.tool()
def odoo_search_read(
    model: str,
    domain: list | None = None,
    fields: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
    order: str | None = None,
) -> str:
    """Consulta registros de un modelo Odoo (search_read).

    model: p.ej. 'product.template', 'account.move', 'res.partner'.
    domain: dominio Odoo, p.ej. [['sale_ok','=',True],['default_code','!=',False]].
            Vacío/None = todos. fields: None trae todos (evítalo en modelos gordos).
    Devuelve JSON con la lista de registros.
    """
    kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
    if fields:
        kwargs["fields"] = fields
    if order:
        kwargs["order"] = order
    return _dump(_call(model, "search_read", [domain or []], kwargs))


@mcp.tool()
def odoo_read(model: str, ids: list[int], fields: list[str] | None = None) -> str:
    """Lee registros concretos por id. fields None = todos los campos."""
    kwargs = {"fields": fields} if fields else {}
    return _dump(_call(model, "read", [ids], kwargs))


@mcp.tool()
def odoo_fields_get(model: str, attributes: list[str] | None = None) -> str:
    """Inspecciona el esquema de un modelo (nombre, tipo, relación, etc.).

    ÚSALO ANTES de escribir campos que no conozcas: la geografía CR de los
    partners usa campos Studio (state_id, x_studio_canton_cr, x_studio_senas…),
    no los del módulo. attributes por defecto: ['string','type','relation','required'].
    """
    attrs = attributes or ["string", "type", "relation", "required", "readonly"]
    return _dump(_call(model, "fields_get", [], {"attributes": attrs}))


@mcp.tool()
def odoo_search_count(model: str, domain: list | None = None) -> str:
    """Cuenta registros que cumplen un dominio."""
    return _dump({"count": _call(model, "search_count", [domain or []])})


@mcp.tool()
def odoo_name_search(model: str, name: str = "", limit: int = 20) -> str:
    """Busca por nombre. Devuelve pares [id, display_name]."""
    return _dump(_call(model, "name_search", [], {"name": name, "limit": limit}))


@mcp.tool()
def odoo_create(model: str, values: dict) -> str:
    """Crea un registro. Devuelve el id nuevo. (Mutación — respeta READONLY.)"""
    _guard_write()
    new_id = _call(model, "create", [values])
    return _dump({"created_id": new_id, "model": model})


@mcp.tool()
def odoo_write(model: str, ids: list[int], values: dict) -> str:
    """Escribe valores en registros existentes. (Mutación — respeta READONLY.)"""
    _guard_write()
    ok = _call(model, "write", [ids, values])
    return _dump({"ok": ok, "model": model, "ids": ids})


@mcp.tool()
def odoo_execute(
    model: str, method: str, args: list | None = None, kwargs: dict | None = None
) -> str:
    """Escape hatch: execute_kw genérico (cualquier método del modelo).

    Para lecturas raras (read_group, default_get…) o acciones. Si el método es de
    escritura y READONLY=1, se bloquea.
    """
    if method in _WRITE_METHODS:
        _guard_write()
    return _dump(_call(model, method, args or [], kwargs or {}))


@mcp.tool()
def odoo_unlink(model: str, ids: list[int]) -> str:
    """Borra registros (IRREVERSIBLE). Requiere ODOO_MCP_ALLOW_UNLINK=1."""
    _guard_write()
    if not ALLOW_UNLINK:
        raise RuntimeError(
            "odoo_unlink bloqueado. Exporta ODOO_MCP_ALLOW_UNLINK=1 para permitir borrados."
        )
    ok = _call(model, "unlink", [ids])
    return _dump({"unlinked": ok, "model": model, "ids": ids})


def _selftest() -> int:
    try:
        uid, _ = _connect()
        ver = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True).version()
        n = _call("product.template", "search_count", [[]])
        print(f"OK · uid={uid} · Odoo {ver.get('server_version')} · product.template={n}")
        print(f"    READONLY={READONLY}  ALLOW_UNLINK={ALLOW_UNLINK}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"FALLO: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    mcp.run()
