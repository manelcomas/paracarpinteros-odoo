<?php
/** GET /buzon-rx/status — estado de la conexión OAuth. Público (sin secretos). */
require_once __DIR__ . '/lib.php';

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') { http_response_code(204); exit; }

try {
    $row = bx_oauth_row();
    if (!$row) {
        bx_json(['connected' => false, 'email' => null]);
    }
    $lastPoll = bx_meta_get('last_poll');
    $pendientes = 0;
    try {
        $pendientes = (int) bx_db()->query("SELECT COUNT(*) FROM xmls_recibidos WHERE estado='pending'")->fetchColumn();
    } catch (Exception $e) {}
    bx_json([
        'connected' => true,
        'email' => $row['email'],
        'expires_at' => intval($row['expires_at']),
        'token_valid' => intval($row['expires_at']) > time(),
        'last_poll' => $lastPoll ? intval($lastPoll) : null,
        'last_poll_human' => $lastPoll ? gmdate('Y-m-d H:i:s', intval($lastPoll)) . ' UTC' : null,
        'pendientes' => $pendientes,
    ]);
} catch (Exception $e) {
    bx_json(['connected' => false, 'error' => $e->getMessage()], 500);
}
