<?php
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');

$crlibreOk = file_exists('/opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php');
$opensslOk = extension_loaded('openssl');
$apiKeyOk = (bool) getenv('SIGNER_API_KEY');

echo json_encode([
    'status' => ($crlibreOk && $opensslOk && $apiKeyOk) ? 'ok' : 'degraded',
    'crlibre' => $crlibreOk,
    'openssl' => $opensslOk,
    'api_key_configured' => $apiKeyOk,
    'php_version' => PHP_VERSION,
    'time' => date('c'),
]);
