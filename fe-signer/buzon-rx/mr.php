<?php
/**
 * POST /buzon-rx/mr — genera + firma + envía el Mensaje Receptor (MR) a Hacienda.
 *
 * Body JSON: {
 *   clave_fe_proveedor : clave (50 díg) de la FE del proveedor en el buzón
 *   tipo               : "05" aceptación · "06" aceptación parcial · "07" rechazo
 *   p12Base64          : certificado .p12 de Gabriela en base64
 *   pin                : PIN del .p12
 *   condicionImpuesto? : 01..05 (def. "01" — crédito IVA general); ignorado en tipo 07
 *   motivo_rechazo?    : texto libre (se recorta a 160 chars); OBLIGATORIO (mín. 10
 *                        chars) en tipo 07; recomendado en 06. Va al <DetalleMensaje>.
 * }
 *
 * Implementa los tres Mensajes Receptor del XSD v4.4:
 *   tipo 05 → <Mensaje>1</Mensaje> (Aceptado)           → estado 'accepted'
 *   tipo 06 → <Mensaje>2</Mensaje> (Aceptación parcial) → estado 'partial'
 *   tipo 07 → <Mensaje>3</Mensaje> (Rechazado)          → estado 'rejected'
 * La firma reutiliza CRLibre con tipoDoc '05': el documento firmado es siempre
 * un <MensajeReceptor> y los tres tipos comparten el mismo namespace de firma.
 * El envío a Hacienda va por el Worker Cloudflare.
 */
require_once __DIR__ . '/lib.php';
require_once '/opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php';

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') { http_response_code(204); exit; }
bx_require_api_key();

// ─── Constantes del emisor del MR (Gabriela Brenes Solano) ───────
const MR_RECEPTOR_CEDULA      = '304410837';
const MR_RECEPTOR_CEDULA_PAD  = '000304410837';   // 12 díg para la clave
const MR_RECEPTOR_TIPO_ID     = '01';             // 01 = física
const MR_SUCURSAL             = '001';
const MR_TERMINAL             = '00001';

$p12Path = null;
try {
    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) bx_json(['error' => 'body debe ser JSON'], 400);

    $clave = trim($body['clave_fe_proveedor'] ?? '');
    $tipo  = $body['tipo'] ?? '';
    $p12B64 = $body['p12Base64'] ?? '';
    $pin    = $body['pin'] ?? '';
    $condImpuesto  = trim($body['condicionImpuesto'] ?? '01');
    $detalleMsg    = trim($body['motivo_rechazo'] ?? '');

    if (!$clave)  bx_json(['error' => 'falta clave_fe_proveedor'], 400);
    if (!in_array($tipo, ['05', '06', '07'], true)) {
        bx_json(['error' => 'tipo debe ser 05 (aceptación), 06 (aceptación parcial) o 07 (rechazo)'], 400);
    }
    if ($tipo === '07' && strlen($detalleMsg) < 10) {
        bx_json(['error' => 'motivo_rechazo obligatorio (min 10 chars) para rechazo'], 400);
    }
    if (!$p12B64 || !$pin) bx_json(['error' => 'faltan p12Base64 / pin'], 400);

    // ─── Cargar la FE del proveedor desde el buzón ───────────────
    $st = bx_db()->prepare("SELECT estado, emisor_cedula, monto_total, moneda, xml_content
                            FROM xmls_recibidos WHERE clave=?");
    $st->execute([$clave]);
    $fe = $st->fetch(PDO::FETCH_ASSOC);
    if (!$fe) bx_json(['error' => 'clave no encontrada en el buzón'], 404);
    if (in_array($fe['estado'], ['accepted','partial','rejected'], true)) {
        bx_json(['error' => 'esta FE ya tiene MR emitido (estado=' . $fe['estado'] . ')'], 409);
    }

    $xmlProv = $fe['xml_content'];

    // ─── Extraer datos de la FE del proveedor ────────────────────
    $g = function ($re) use ($xmlProv) {
        return preg_match($re, $xmlProv, $m) ? trim($m[1]) : '';
    };
    $provCedula   = $fe['emisor_cedula'] ?: $g('/<Emisor>.*?<Identificacion>.*?<Numero>([^<]+)<\/Numero>/s');
    $provTipoId   = $g('/<Emisor>.*?<Identificacion>.*?<Tipo>([^<]+)<\/Tipo>/s') ?: '01';
    $totalFactura = $g('/<TotalComprobante>([\d.]+)<\/TotalComprobante>/');
    $totalImp     = $g('/<TotalImpuesto>([\d.]+)<\/TotalImpuesto>/');
    if ($totalFactura === '') $totalFactura = (string) ($fe['monto_total'] ?: 0);
    if ($provCedula === '')   bx_json(['error' => 'no se pudo extraer la cédula del proveedor del XML'], 422);

    $fmt = fn($n) => number_format((float)$n, 5, '.', '');

    // ─── Reservar consecutivo MR (atómico) ───────────────────────
    $pdo = bx_db();
    $pdo->beginTransaction();
    $pdo->prepare("UPDATE consec_mr SET ultimo_consec = ultimo_consec + 1 WHERE tipo=?")
        ->execute([$tipo]);
    $sel = $pdo->prepare("SELECT ultimo_consec FROM consec_mr WHERE tipo=?");
    $sel->execute([$tipo]);
    $correlativo = (int) $sel->fetchColumn();
    $pdo->commit();
    if ($correlativo < 1) {
        bx_json([
            'error' => 'la tabla consec_mr no tiene fila para este tipo',
            'tipo' => $tipo,
            'seed_sql' => 'INSERT INTO consec_mr (tipo, ultimo_consec) VALUES (?, 0)',
        ], 500);
    }

    // El consecutivo ya quedó reservado y commiteado (lock corto). Si el MR NO llega
    // a enviarse a Hacienda (falla la firma o el token), lo recuperamos con un
    // compare-and-swap para no dejar hueco; una vez enviado, el número queda usado.
    $mrEnviado = false;
    $reclaimConsec = function () use ($pdo, $tipo, $correlativo) {
        try {
            $pdo->prepare("UPDATE consec_mr SET ultimo_consec = ultimo_consec - 1 WHERE tipo=? AND ultimo_consec=?")
                ->execute([$tipo, $correlativo]);
        } catch (Throwable $e) { /* best-effort: si no se puede, queda el hueco */ }
    };

    // Mapa tipo MR → valor del nodo <Mensaje> (XSD v4.4) y estado del buzón.
    $mensajeVal  = ['05' => '1', '06' => '2', '07' => '3'][$tipo];
    $estadoFinal = ['05' => 'accepted', '06' => 'partial', '07' => 'rejected'][$tipo];

    // ─── Construir consecutivo (20) y clave (50) del MR ──────────
    // Consecutivo = Sucursal(3) + Terminal(5) + Tipo(2) + Correlativo(10)
    $consecutivoMR = MR_SUCURSAL . MR_TERMINAL . $tipo
                   . str_pad((string)$correlativo, 10, '0', STR_PAD_LEFT);
    // Clave = 506 + ddmmyy + cedulaPad(12) + consecutivo(20) + situacion(1) + seguridad(8)
    $cr = new DateTime('now', new DateTimeZone('America/Costa_Rica'));
    $fechaClave = $cr->format('dmy');
    $situacion  = '1';
    $seguridad  = str_pad((string) random_int(0, 99999999), 8, '0', STR_PAD_LEFT);
    $claveMR    = '506' . $fechaClave . MR_RECEPTOR_CEDULA_PAD
                . $consecutivoMR . $situacion . $seguridad;
    $fechaEmisionDoc = $cr->format('Y-m-d\TH:i:sP');  // ISO con offset -06:00

    // ─── Construir el XML MensajeReceptor (XSD v4.4) ─────────────
    $NS = 'https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/mensajeReceptor';
    $esc = fn($s) => htmlspecialchars($s, ENT_XML1 | ENT_QUOTES, 'UTF-8');
    $xml  = '<?xml version="1.0" encoding="UTF-8"?>';
    $xml .= '<MensajeReceptor xmlns="' . $NS . '">';
    $xml .= '<Clave>' . $claveMR . '</Clave>';
    $xml .= '<NumeroCedulaEmisor>' . $esc($provCedula) . '</NumeroCedulaEmisor>';
    $xml .= '<FechaEmisionDoc>' . $fechaEmisionDoc . '</FechaEmisionDoc>';
    $xml .= '<Mensaje>' . $mensajeVal . '</Mensaje>';  // 1=Aceptado 2=Parcial 3=Rechazado
    if ($detalleMsg !== '') {
        $xml .= '<DetalleMensaje>' . $esc(mb_substr($detalleMsg, 0, 160)) . '</DetalleMensaje>';
    }
    // En un rechazo (07) el receptor no acredita IVA: se omite el bloque de impuesto.
    if ($tipo !== '07' && $totalImp !== '' && (float)$totalImp > 0) {
        $xml .= '<MontoTotalImpuesto>' . $fmt($totalImp) . '</MontoTotalImpuesto>';
        if (in_array($condImpuesto, ['01','02','03','04','05'], true)) {
            $xml .= '<CondicionImpuesto>' . $condImpuesto . '</CondicionImpuesto>';
        }
    }
    $xml .= '<TotalFactura>' . $fmt($totalFactura) . '</TotalFactura>';
    $xml .= '<NumeroCedulaReceptor>' . MR_RECEPTOR_CEDULA . '</NumeroCedulaReceptor>';
    $xml .= '<NumeroConsecutivoReceptor>' . $consecutivoMR . '</NumeroConsecutivoReceptor>';
    $xml .= '</MensajeReceptor>';

    // ─── Firmar con CRLibre ──────────────────────────────────────
    // Siempre tipoDoc '05': los tres MR (05/06/07) son <MensajeReceptor> y
    // comparten el mismo namespace de firma; el tipo solo cambia el
    // consecutivo y el nodo <Mensaje>, no el documento que se firma.
    $p12Bytes = base64_decode($p12B64, true);
    if ($p12Bytes === false || strlen($p12Bytes) < 100) {
        $reclaimConsec();
        bx_json(['error' => 'p12Base64 inválido'], 400);
    }
    $certsTmp = [];
    if (!@openssl_pkcs12_read($p12Bytes, $certsTmp, $pin)) {
        $reclaimConsec();
        bx_json(['error' => 'p12 inválido o PIN incorrecto'], 400);
    }
    unset($certsTmp);
    $p12Path = tempnam(sys_get_temp_dir(), 'mrp12_');
    file_put_contents($p12Path, $p12Bytes);
    chmod($p12Path, 0600);

    $firmador = new Firmadocr();
    $signedB64 = $firmador->firmar($p12Path, $pin, base64_encode($xml), '05');
    @unlink($p12Path);
    $p12Path = null;
    if (!$signedB64) { $reclaimConsec(); bx_json(['error' => 'la firma del MR devolvió vacío'], 500); }
    $mrXmlFirmado = base64_decode($signedB64);

    // ─── Enviar a Hacienda vía el Worker Cloudflare ──────────────
    $workerUrl = rtrim(bx_env('HACIENDA_WORKER_URL',
        'https://misty-cake-937c.lacarpicr.workers.dev'), '/');
    $tokResp = bx_http('POST', $workerUrl . '/token', [
        'headers' => ['Content-Type: application/json'],
        'body' => json_encode(['client_id' => 'api-prod']),
    ]);
    $tok = json_decode($tokResp['body'], true);
    if (empty($tok['access_token'])) {
        $reclaimConsec();
        bx_json(['error' => 'no se obtuvo token del Worker', 'detail' => substr($tokResp['body'],0,300)], 502);
    }
    // A partir de aquí se envía el MR a Hacienda: el consecutivo se da por usado
    // (aunque Hacienda rechace o falle la red, no se reusa para no duplicar clave).
    $mrEnviado = true;
    $submit = bx_http('POST', $workerUrl . '/submit', [
        'headers' => ['Content-Type: application/json'],
        'body' => json_encode([
            'token' => $tok['access_token'],
            'clave' => $claveMR,
            'fecha' => $fechaEmisionDoc,
            'emisor'   => ['tipoIdentificacion' => MR_RECEPTOR_TIPO_ID,
                           'numeroIdentificacion' => MR_RECEPTOR_CEDULA],
            'receptor' => ['tipoIdentificacion' => $provTipoId,
                           'numeroIdentificacion' => $provCedula],
            'comprobanteXml' => $signedB64,
        ]),
    ]);
    $respHacienda = $submit['body'];
    $enviadoOk = ($submit['code'] >= 200 && $submit['code'] < 300);

    // ─── Guardar resultado en el buzón ───────────────────────────
    $upd = bx_db()->prepare("UPDATE xmls_recibidos
        SET estado=?, mr_clave=?, mr_xml=?, mr_respuesta_hacienda=?, fecha_mr=?
        WHERE clave=?");
    $upd->execute([
        $enviadoOk ? $estadoFinal : 'requires_manual',
        $claveMR, $mrXmlFirmado, $respHacienda, time(), $clave,
    ]);

    bx_json([
        'ok' => $enviadoOk,
        'tipo' => $tipo,
        'clave_fe_proveedor' => $clave,
        'clave_mr' => $claveMR,
        'consecutivo_mr' => $consecutivoMR,
        'estado' => $enviadoOk ? $estadoFinal : 'requires_manual',
        'hacienda_http' => $submit['code'],
        'hacienda_respuesta' => substr($respHacienda, 0, 500),
    ], $enviadoOk ? 200 : 502);

} catch (Exception $e) {
    if ($p12Path && file_exists($p12Path)) @unlink($p12Path);
    if (bx_db()->inTransaction()) bx_db()->rollBack();
    if (empty($mrEnviado) && isset($reclaimConsec)) $reclaimConsec();
    bx_json(['ok' => false, 'error' => $e->getMessage()], 500);
} catch (Throwable $e) {
    if ($p12Path && file_exists($p12Path)) @unlink($p12Path);
    if (empty($mrEnviado) && isset($reclaimConsec)) $reclaimConsec();
    bx_json(['ok' => false, 'error' => 'fatal: ' . $e->getMessage()], 500);
}
