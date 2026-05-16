<?php
/** GET /buzon-rx/list?status=pending|accepted|...&since=ts — lista XMLs recibidos. */
require_once __DIR__ . '/lib.php';

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') { http_response_code(204); exit; }

try {
    $status = $_GET['status'] ?? '';
    $since = isset($_GET['since']) ? intval($_GET['since']) : 0;
    $limit = min(200, max(1, intval($_GET['limit'] ?? 100)));

    $sql = "SELECT clave,gmail_msg_id,fecha_recibido,emisor_cedula,emisor_nombre,
                   receptor_cedula,consecutivo,fecha_emision,monto_total,moneda,
                   estado,mr_clave,fecha_mr,motivo_rechazo
            FROM xmls_recibidos WHERE 1=1";
    $args = [];
    if ($status && $status !== 'todas' && $status !== 'all') {
        // permitir alias: pendientes->pending, etc.
        $map = ['pendientes'=>'pending','aceptadas'=>'accepted','rechazadas'=>'rejected','parciales'=>'partial'];
        $st = $map[$status] ?? $status;
        $sql .= " AND estado=?";
        $args[] = $st;
    }
    if ($since > 0) { $sql .= " AND fecha_recibido >= ?"; $args[] = $since; }
    $sql .= " ORDER BY fecha_recibido DESC LIMIT " . $limit;

    $stm = bx_db()->prepare($sql);
    $stm->execute($args);
    $rows = $stm->fetchAll(PDO::FETCH_ASSOC);

    // Plazo legal: 8 días hábiles desde recepción (aprox: 8 días naturales + margen)
    foreach ($rows as &$r) {
        $r['fecha_recibido'] = intval($r['fecha_recibido']);
        $r['monto_total'] = floatval($r['monto_total']);
        $diasTrans = floor((time() - $r['fecha_recibido']) / 86400);
        $r['dias_restantes'] = 8 - $diasTrans;  // aproximado
        $r['plazo_vencido'] = ($r['dias_restantes'] < 0);
    }
    unset($r);

    $counts = [];
    foreach (bx_db()->query("SELECT estado,COUNT(*) c FROM xmls_recibidos GROUP BY estado") as $cr) {
        $counts[$cr['estado']] = intval($cr['c']);
    }

    bx_json(['ok' => true, 'total' => count($rows), 'counts' => $counts, 'items' => $rows]);
} catch (Exception $e) {
    bx_json(['ok' => false, 'error' => $e->getMessage()], 500);
}
