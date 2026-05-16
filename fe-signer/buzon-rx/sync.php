<?php
/** POST /buzon-rx/sync — fuerza un poll inmediato. Requiere X-API-Key. */
require_once __DIR__ . '/lib.php';
require_once __DIR__ . '/poll.php';

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') { http_response_code(204); exit; }
bx_require_api_key();

try {
    $res = bx_poll();
    bx_json(['ok' => true, 'resumen' => $res]);
} catch (Exception $e) {
    bx_json(['ok' => false, 'error' => $e->getMessage()], 500);
}
