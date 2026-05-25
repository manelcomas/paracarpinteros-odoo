<?php
/** GET /alibaba-rx/status — estado de la conexión OAuth + última sync + contadores. */
require_once __DIR__ . '/lib.php';
arx_require_api_key();

$row = arx_oauth_row();
$pdo = arx_db();
$counts = [];
foreach (['pedidos','eventos','envios_shenzhen','contactos'] as $t) {
    $counts[$t] = intval($pdo->query("SELECT COUNT(*) FROM $t")->fetchColumn());
}
$estados = [];
foreach ($pdo->query("SELECT estado, COUNT(*) c FROM pedidos GROUP BY estado") as $r) {
    $estados[$r['estado']] = intval($r['c']);
}

arx_json([
    'connected' => !empty($row),
    'email' => $row['email'] ?? null,
    'expires_at' => intval($row['expires_at'] ?? 0),
    'connected_at' => intval(arx_meta_get('connected_at', 0)),
    'last_poll' => intval(arx_meta_get('last_poll', 0)),
    'last_poll_resumen' => json_decode(arx_meta_get('last_poll_resumen', 'null'), true),
    'counts' => $counts,
    'estados' => $estados,
]);
