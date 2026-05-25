"""
Mini-cargador del .env raíz del proyecto.

Uso desde cualquier script en scripts/:

    from _env import load_project_env
    load_project_env()              # carga .env del proyecto en os.environ
    api_key = os.environ['ODOO_API_KEY']

Sin dependencias externas. Acepta comentarios con # y valores entre comillas.
No sobreescribe variables ya presentes en el entorno (export en shell gana).
"""
from __future__ import annotations

import os
from pathlib import Path


def load_project_env(path: str | os.PathLike | None = None) -> None:
    if path is None:
        path = Path(__file__).resolve().parent.parent / ".env"
    path = Path(path)
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)
