<?php
/** POST /alibaba-rx/sync — fuerza un poll inmediato del Gmail. */
require_once __DIR__ . '/lib.php';
require_once __DIR__ . '/poll.php';

arx_require_api_key();
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') { http_response_code(204); exit; }

try {
    $resumen = arx_poll();
    arx_json(['ok' => true, 'resumen' => $resumen]);
} catch (Exception $e) {
    arx_json(['ok' => false, 'error' => $e->getMessage()], 500);
}
