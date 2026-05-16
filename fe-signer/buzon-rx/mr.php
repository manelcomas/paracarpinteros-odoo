<?php
/**
 * POST /buzon-rx/mr — genera + firma + envía el Mensaje Receptor.
 * Body JSON: { clave_fe_proveedor, tipo: "05"|"06"|"07", motivo_rechazo?, p12Base64?, pin? }
 *
 * BLOQUE 1: esqueleto. La generación completa del XML MR, la firma y el envío
 * a Hacienda se implementan en el Bloque 4. Este endpoint ya valida la entrada
 * y reserva el consecutivo MR de forma atómica.
 */
require_once __DIR__ . '/lib.php';

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') { http_response_code(204); exit; }
bx_require_api_key();

try {
    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) bx_json(['error' => 'body debe ser JSON'], 400);

    $clave = $body['clave_fe_proveedor'] ?? '';
    $tipo = $body['tipo'] ?? '';
    if (!$clave) bx_json(['error' => 'falta clave_fe_proveedor'], 400);
    if (!in_array($tipo, ['05','06','07'], true)) bx_json(['error' => 'tipo debe ser 05/06/07'], 400);
    if ($tipo === '07' && strlen(trim($body['motivo_rechazo'] ?? '')) < 10) {
        bx_json(['error' => 'motivo_rechazo obligatorio (min 10 chars) para rechazo'], 400);
    }

    // Verificar que el XML existe en la DB
    $st = bx_db()->prepare("SELECT estado FROM xmls_recibidos WHERE clave=?");
    $st->execute([$clave]);
    $estado = $st->fetchColumn();
    if ($estado === false) bx_json(['error' => 'clave no encontrada en buzón'], 404);
    if (in_array($estado, ['accepted','partial','rejected'], true)) {
        bx_json(['error' => 'esta FE ya tiene MR emitido (estado=' . $estado . ')'], 409);
    }

    // Reservar consecutivo MR atómicamente
    $pdo = bx_db();
    $pdo->beginTransaction();
    $upd = $pdo->prepare("UPDATE consec_mr SET ultimo_consec = ultimo_consec + 1 WHERE tipo=?");
    $upd->execute([$tipo]);
    $sel = $pdo->prepare("SELECT ultimo_consec FROM consec_mr WHERE tipo=?");
    $sel->execute([$tipo]);
    $consec = (int) $sel->fetchColumn();
    $pdo->commit();

    bx_json([
        'ok' => false,
        'pending_block4' => true,
        'mensaje' => 'Bloque 1: endpoint validado y consecutivo MR reservado. '
                   . 'La generación/firma/envío del MR se implementa en el Bloque 4.',
        'tipo' => $tipo,
        'clave_fe' => $clave,
        'consecutivo_mr_reservado' => str_pad((string)$consec, 10, '0', STR_PAD_LEFT),
    ], 501);
} catch (Exception $e) {
    if (bx_db()->inTransaction()) bx_db()->rollBack();
    bx_json(['ok' => false, 'error' => $e->getMessage()], 500);
}
