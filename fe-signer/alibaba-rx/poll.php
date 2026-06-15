<?php
/**
 * poll.php — descubre correos nuevos de Alibaba en el Gmail conectado.
 * Lo invoca el cron cada 15 min, o /alibaba-rx/sync manualmente.
 * Aplica "uno mata al otro": cada correo actualiza el estado vigente del pedido.
 */
require_once __DIR__ . '/lib.php';
require_once __DIR__ . '/parser.php';

// Acceso web a poll.php exige API key (el cron por CLI no la necesita).
if (php_sapi_name() !== 'cli') {
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') { http_response_code(204); exit; }
    arx_require_api_key();
}

function arx_poll() {
    $resumen = ['mensajes' => 0, 'pedidos_nuevos' => 0, 'pedidos_actualizados' => 0,
                'eventos_nuevos' => 0, 'duplicados' => 0, 'contactos' => 0, 'errores' => []];

    $token = arx_get_access_token(); // lanza Exception si no conectado
    $auth = ['Authorization: Bearer ' . $token];

    // Cargar mapa labelId → nombre proveedor (cacheado static)
    $proveedor_labels = arx_load_proveedor_labels($auth);
    $resumen['proveedor_labels_cargados'] = count($proveedor_labels);

    // Query Gmail: solo correos de Alibaba en los últimos N días
    $days = intval(arx_env('ALIBABA_LOOKBACK_DAYS', '365'));
    $query = arx_env('ALIBABA_GMAIL_QUERY',
        'from:alibaba.com OR from:notice.alibaba.com OR from:email.alibaba.com OR from:service.alibaba.com '
        . 'newer_than:' . $days . 'd');

    // Paginar todos los mensajes que coincidan
    $maxPages = 10;
    $pageToken = null;
    $page = 0;
    do {
        $url = 'https://gmail.googleapis.com/gmail/v1/users/me/messages?q=' . rawurlencode($query) . '&maxResults=100';
        if ($pageToken) $url .= '&pageToken=' . rawurlencode($pageToken);
        $listResp = arx_http('GET', $url, ['headers' => $auth]);
        if ($listResp['code'] !== 200) {
            throw new Exception('gmail list HTTP ' . $listResp['code'] . ': ' . substr($listResp['body'], 0, 200));
        }
        $list = json_decode($listResp['body'], true);
        $messages = $list['messages'] ?? [];
        $resumen['mensajes'] += count($messages);
        foreach ($messages as $m) {
            try { arx_process_msg($m['id'], $auth, $resumen, $proveedor_labels); }
            catch (Exception $e) { $resumen['errores'][] = $m['id'] . ': ' . $e->getMessage(); }
        }
        $pageToken = $list['nextPageToken'] ?? null;
        $page++;
    } while ($pageToken && $page < $maxPages);

    arx_meta_set('last_poll', time());
    arx_meta_set('last_poll_resumen', json_encode($resumen));
    return $resumen;
}

function arx_process_msg($msgId, $auth, &$resumen, $proveedor_labels = []) {
    $pdo = arx_db();

    // ¿ya procesado en eventos?
    $chk = $pdo->prepare("SELECT 1 FROM eventos WHERE gmail_msg_id=?");
    $chk->execute([$msgId]);
    if ($chk->fetchColumn()) { $resumen['duplicados']++; return; }
    $chkC = $pdo->prepare("SELECT 1 FROM contactos WHERE gmail_msg_id=?");
    $chkC->execute([$msgId]);
    if ($chkC->fetchColumn()) { $resumen['duplicados']++; return; }

    // format=full nos da headers + labelIds + el body HTML completo (necesario para extraer items)
    $msgResp = arx_http('GET',
        'https://gmail.googleapis.com/gmail/v1/users/me/messages/' . $msgId . '?format=full',
        ['headers' => $auth]);
    if ($msgResp['code'] !== 200) {
        $resumen['errores'][] = "msg $msgId: HTTP " . $msgResp['code'];
        return;
    }
    $msg = json_decode($msgResp['body'], true);
    $parsed = arx_parse_alibaba($msg, $proveedor_labels);
    if (!$parsed) return; // no relevante

    if ($parsed['tipo'] === 'cotizacion' || $parsed['tipo'] === 'inquiry' || $parsed['tipo'] === 'soporte') {
        $st = $pdo->prepare("INSERT OR IGNORE INTO contactos(tipo,proveedor,asunto,gmail_msg_id,ts)
            VALUES(?,?,?,?,?)");
        $st->execute([$parsed['tipo'], $parsed['proveedor'] ?? null, $parsed['asunto'], $msgId, $parsed['ts']]);
        if ($st->rowCount() > 0) $resumen['contactos']++;
        return;
    }

    // pedido
    $extra = [
        'comprador' => $parsed['comprador'] ?? 'desconocido',
        'proveedor' => $parsed['proveedor'] ?? null,
        'monto' => $parsed['monto'] ?? null,
        'moneda' => $parsed['moneda'] ?? null,
        'asunto' => $parsed['asunto'],
        'gmail_msg_id' => $msgId,
        'ts' => $parsed['ts'],
    ];
    $r = arx_upsert_pedido(
        $parsed['numero'], $parsed['estado'], $parsed['estado_etiqueta'], $parsed['prioridad'], $extra
    );
    if ($r === 'inserted') $resumen['pedidos_nuevos']++;
    elseif ($r === 'updated') $resumen['pedidos_actualizados']++;

    if (arx_log_evento($parsed['numero'], $msgId, $parsed['estado'], $parsed['estado_etiqueta'],
                        $parsed['asunto'], $parsed['ts'])) {
        $resumen['eventos_nuevos']++;
    }

    // Extraer items (productos) del body HTML — sólo si está disponible
    $html = arx_find_html_body($msg['payload'] ?? []);
    if ($html) {
        $items = arx_extract_items_from_html($html);
        if ($items) {
            $saved = arx_save_items($parsed['numero'], $items, $msgId);
            $resumen['items_guardados'] = ($resumen['items_guardados'] ?? 0) + $saved;
        }
    }
}

/** Encuentra el body HTML en la estructura recursiva del mensaje Gmail. */
function arx_find_html_body($part) {
    if (($part['mimeType'] ?? '') === 'text/html' && isset($part['body']['data'])) {
        return base64_decode(strtr($part['body']['data'], '-_', '+/'));
    }
    foreach ($part['parts'] ?? [] as $p) {
        $h = arx_find_html_body($p);
        if ($h) return $h;
    }
    return null;
}

// CLI invocation (cron) or include
if (php_sapi_name() === 'cli' || (isset($argv) && count($argv) > 0 && basename($argv[0]) === 'poll.php')) {
    try {
        $r = arx_poll();
        echo json_encode($r, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . "\n";
    } catch (Exception $e) {
        fwrite(STDERR, 'poll error: ' . $e->getMessage() . "\n");
        exit(1);
    }
}
