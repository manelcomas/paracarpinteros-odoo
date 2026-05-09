# -*- coding: utf-8 -*-
"""
FastAPI application con scheduler integrado.
- Corre un worker APScheduler cada N minutos (POLL_INTERVAL_MINUTES)
- Expone endpoints para status / trigger manual / test de conexión
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import Depends, FastAPI, HTTPException, Header

from .config import settings
from .processor import Processor
from .api_panel import router as panel_router

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
_logger = logging.getLogger('correos-bridge')

# Estado global del servicio
state = {
    'last_run_at': None,
    'last_run_stats': None,
    'total_runs': 0,
    'started_at': datetime.now().isoformat(),
}

processor = Processor()
scheduler = BackgroundScheduler(timezone='America/Costa_Rica')


def run_worker():
    try:
        stats = processor.run_once()
        state['last_run_at'] = datetime.now().isoformat()
        state['last_run_stats'] = stats
        state['total_runs'] += 1
        _logger.info("Worker run OK: %s", stats)
    except Exception as e:
        _logger.exception("Worker run FAILED: %s", e)
        state['last_run_stats'] = {'error': str(e)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Arranque: scheduler condicional
    _logger.info("Correos CR bridge arrancando...")
    # WORKER_AUTO=0 desactiva el polling automático (generación solo desde panel)
    auto = os.environ.get('WORKER_AUTO', '0').strip() not in ('0', 'false', 'no', '')
    if auto:
        scheduler.add_job(
            run_worker,
            trigger=IntervalTrigger(minutes=settings.poll_interval_minutes),
            id='poll-pickings',
            next_run_time=datetime.now(),
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        _logger.info("Scheduler arrancado (intervalo %d min)", settings.poll_interval_minutes)
    else:
        _logger.info("WORKER_AUTO=0 → polling automático DESACTIVADO. Generación solo desde panel.")
    yield
    if auto:
        scheduler.shutdown(wait=False)
    _logger.info("Bridge detenido.")


app = FastAPI(
    title='Correos CR Bridge',
    description='Microservicio que conecta Odoo Online con el WS de Correos de Costa Rica',
    version='1.0.0',
    lifespan=lifespan,
)

# CORS — permite llamadas desde el panel servido en panel.paracarpinteros.com
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        'http://panel.paracarpinteros.com',
        'https://panel.paracarpinteros.com',
        'http://66.94.99.220',
        'http://localhost',
        'http://127.0.0.1',
    ],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Registrar router del panel
app.include_router(panel_router)


def verify_token(x_api_token: str = Header(None)):
    if x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail='Invalid API token')


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'correos-cr-bridge'}


@app.get('/status', dependencies=[Depends(verify_token)])
def status():
    return {
        'started_at': state['started_at'],
        'last_run_at': state['last_run_at'],
        'total_runs': state['total_runs'],
        'last_run_stats': state['last_run_stats'],
        'poll_interval_minutes': settings.poll_interval_minutes,
        'correos_env': settings.correos_env,
    }


@app.post('/process-now', dependencies=[Depends(verify_token)])
def process_now():
    """Dispara una pasada manualmente (útil para debug)."""
    stats = processor.run_once()
    state['last_run_at'] = datetime.now().isoformat()
    state['last_run_stats'] = stats
    state['total_runs'] += 1
    return stats


@app.get('/test-correos', dependencies=[Depends(verify_token)])
def test_correos():
    """Prueba conexión con Correos CR (token + provincias)."""
    try:
        token = processor.correos.get_token()
        provs = processor.correos.get_provincias()
        return {
            'ok': True,
            'token_received': bool(token),
            'provincias_count': len(provs),
            'provincias': provs,
        }
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@app.get('/test-odoo', dependencies=[Depends(verify_token)])
def test_odoo():
    """Prueba conexión con Odoo (autenticación XML-RPC)."""
    try:
        uid = processor.odoo.authenticate()
        pickings = processor.odoo.search_pickings_pendientes(limit=5)
        return {
            'ok': True,
            'uid': uid,
            'pickings_pendientes': len(pickings),
            'sample': [{'name': p['name'], 'partner': p['partner_id'][1] if p.get('partner_id') else None}
                       for p in pickings],
        }
    except Exception as e:
        return {'ok': False, 'error': str(e)}
