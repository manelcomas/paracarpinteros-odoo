<?php
/**
 * fe-signer — endpoint de firma XAdES-EPES para Hacienda CR.
 *
 * Recibe XML sin firmar + .p12 + PIN, devuelve XML firmado.
 * Usa CRLibre/API_Hacienda como librería de firma (probada en producción CR).
 *
 * El .p12 nunca se almacena en disco más allá del request en curso.
 *
 * Auth: header X-API-Key debe coincidir con env SIGNER_API_KEY.
 */

// PHP 8.2: silenciar deprecation warnings de CRLibre (propiedades dinámicas).
// display_errors=0 evita que cualquier warning corrompa el output JSON.
// log_errors=1 manda los avisos al error_log de Apache (docker logs los recoge).
ini_set('display_errors', '0');
ini_set('log_errors', '1');
error_reporting(E_ALL & ~E_DEPRECATED & ~E_USER_DEPRECATED & ~E_STRICT);

require_once '/opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php';

// CORS para llamadas desde paracarpinteros.com (el conversor)
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
header('Content-Type: application/json');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'method not allowed, use POST']);
    exit;
}

// Auth
$expected = getenv('SIGNER_API_KEY');
$received = $_SERVER['HTTP_X_API_KEY'] ?? '';
if (!$expected) {
    http_response_code(500);
    echo json_encode(['error' => 'SIGNER_API_KEY not configured on server']);
    exit;
}
if (!hash_equals($expected, $received)) {
    http_response_code(401);
    echo json_encode(['error' => 'invalid or missing X-API-Key']);
    exit;
}

// Parse body
$raw = file_get_contents('php://input');
$body = json_decode($raw, true);
if (!is_array($body)) {
    http_response_code(400);
    echo json_encode(['error' => 'body must be JSON']);
    exit;
}

foreach (['xmlBase64', 'p12Base64', 'pin', 'tipoDoc'] as $field) {
    if (!isset($body[$field]) || $body[$field] === '') {
        http_response_code(400);
        echo json_encode(['error' => "missing field: $field"]);
        exit;
    }
}

$xmlB64   = $body['xmlBase64'];
$p12B64   = $body['p12Base64'];
$pin      = $body['pin'];
$tipoDoc  = $body['tipoDoc']; // '01' factura, '02' nota debito, '03' nota credito, '04' tiquete, '05'-'07' mensaje receptor
// El conversor JS envía '001'/'002'/etc. (3 dígitos) pero CRLibre tiene las
// keys del array $NODOS_NS con 2 dígitos ('01'/'02'/etc.). Si no normalizamos,
// $NODOS_NS[$tipoDoc] = undefined y el namespace default queda TRUNCADO sin
// 'facturaElectronica' al final → digest de KeyInfo/SignedProperties no coincide
// con el documento real → Hacienda rechaza "El XML fue modificado…".
$tipoDoc = str_pad(ltrim($tipoDoc, '0') ?: '0', 2, '0', STR_PAD_LEFT);

// Escribir .p12 a archivo temporal (CRLibre lo necesita como path)
$p12Path = tempnam(sys_get_temp_dir(), 'p12_');
$p12Bytes = base64_decode($p12B64, true);
if ($p12Bytes === false || strlen($p12Bytes) < 100) {
    http_response_code(400);
    echo json_encode(['error' => 'p12Base64 inválido']);
    exit;
}
file_put_contents($p12Path, $p12Bytes);
chmod($p12Path, 0600);

// Pre-validar el .p12 con el PIN ANTES de pasárselo a CRLibre.
// Razón: CRLibre hace `echo ...; exit;` (no excepción) si el .p12 o PIN son
// inválidos — ese exit bypasea try/catch y output buffering, corrompiendo el
// JSON de respuesta. Validar aquí permite devolver un JSON limpio con detalle.
$certsTmp = [];
if (!@openssl_pkcs12_read($p12Bytes, $certsTmp, $pin)) {
    @unlink($p12Path);
    $err = '';
    while ($e = openssl_error_string()) { $err .= $e . '; '; }
    http_response_code(400);
    echo json_encode([
        'error' => 'p12 inválido o PIN incorrecto',
        'detail' => trim($err, '; ') ?: 'openssl_pkcs12_read devolvió false sin detalle',
    ]);
    exit;
}
unset($certsTmp);

// Red de seguridad: si CRLibre escribe al stdout y hace exit en otro path,
// este callback envuelve el output spurio en JSON antes del flush final.
// Si nuestro echo json_encode() emite JSON válido, pasa intacto.
ob_start(function ($buffer) {
    $trimmed = ltrim($buffer);
    if ($trimmed === '' || $trimmed[0] === '{') {
        return $buffer;
    }
    error_log('fe-signer: CRLibre stdout spurio capturado: ' . substr($buffer, 0, 1000));
    http_response_code(500);
    return json_encode([
        'error' => 'CRLibre escribió al stdout en vez de devolver',
        'detail' => substr($buffer, 0, 2000),
    ]);
});

try {
    // CRLibre espera el XML SIN firmar en base64, devuelve el firmado en base64
    $firmador = new Firmadocr();
    $signedB64 = $firmador->firmar($p12Path, $pin, $xmlB64, $tipoDoc);
    $spurious = ob_get_clean();
    if ($spurious !== '' && $spurious !== false) {
        error_log('fe-signer CRLibre stdout (descartado): ' . substr($spurious, 0, 1000));
    }

    if (!$signedB64) {
        http_response_code(500);
        echo json_encode([
            'error' => 'CRLibre devolvió respuesta vacía',
            'detail' => substr((string)$spurious, 0, 500),
        ]);
        exit;
    }

    echo json_encode([
        'signedXmlBase64' => $signedB64,
        'signer' => 'CRLibre',
        'version' => 'v1',
    ]);
} catch (Throwable $e) {
    $spurious = ob_get_clean();
    error_log('fe-signer error: ' . $e->getMessage() . ' stdout=' . substr((string)$spurious, 0, 500));
    http_response_code(500);
    echo json_encode([
        'error' => 'firma falló',
        'detail' => $e->getMessage(),
        'crlibreStdout' => substr((string)$spurious, 0, 500),
    ]);
} finally {
    // Borrar el .p12 temporal SIEMPRE
    if (file_exists($p12Path)) {
        @unlink($p12Path);
    }
}
