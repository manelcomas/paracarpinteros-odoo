<?php
/** GET /alibaba-rx/oauth-callback?code=...&state=... — intercambia code por tokens. */
require_once __DIR__ . '/lib.php';

function arx_page($title, $msg, $ok = true) {
    $color = $ok ? '#10B981' : '#EF4444';
    $icon = $ok ? '✓' : '✗';
    http_response_code($ok ? 200 : 400);
    header('Content-Type: text/html; charset=utf-8');
    echo '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
       . '<meta name="viewport" content="width=device-width,initial-scale=1">'
       . '<title>' . htmlspecialchars($title) . '</title></head>'
       . '<body style="font-family:system-ui,sans-serif;background:#f5f5f7;display:flex;'
       . 'align-items:center;justify-content:center;min-height:100vh;margin:0;">'
       . '<div style="background:#fff;border-radius:14px;padding:36px 44px;max-width:440px;'
       . 'text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.1);">'
       . '<div style="font-size:3rem;color:' . $color . ';">' . $icon . '</div>'
       . '<h2 style="margin:10px 0;">' . htmlspecialchars($title) . '</h2>'
       . '<p style="color:#555;font-size:.95rem;">' . $msg . '</p>'
       . '<p style="color:#999;font-size:.8rem;margin-top:20px;">Podés cerrar esta ventana y volver al panel.</p>'
       . '</div></body></html>';
    exit;
}

$code = $_GET['code'] ?? '';
$state = $_GET['state'] ?? '';
$error = $_GET['error'] ?? '';

if ($error) arx_page('Autorización cancelada', 'Google devolvió: ' . htmlspecialchars($error), false);
if (!$code || !$state) arx_page('Faltan parámetros', 'No se recibió code o state de Google.', false);

// Validar state
$st = arx_db()->prepare("SELECT state FROM oauth_state WHERE state=?");
$st->execute([$state]);
if (!$st->fetchColumn()) arx_page('State inválido', 'El parámetro state no coincide (posible CSRF o expirado).', false);
arx_db()->prepare("DELETE FROM oauth_state WHERE state=?")->execute([$state]);

try {
    $resp = arx_http('POST', 'https://oauth2.googleapis.com/token', [
        'headers' => ['Content-Type: application/x-www-form-urlencoded'],
        'body' => http_build_query([
            'client_id' => arx_env('GOOGLE_OAUTH_CLIENT_ID'),
            'client_secret' => arx_env('GOOGLE_OAUTH_CLIENT_SECRET'),
            'code' => $code,
            'redirect_uri' => arx_oauth_redirect(),
            'grant_type' => 'authorization_code',
        ]),
    ]);
    $d = json_decode($resp['body'], true);
    if ($resp['code'] !== 200 || empty($d['access_token'])) {
        arx_page('Error al obtener tokens', 'Google: ' . htmlspecialchars(substr($resp['body'], 0, 300)), false);
    }
    if (empty($d['refresh_token'])) {
        arx_page('Sin refresh_token',
            'Google no devolvió refresh_token. Revoca el acceso en '
            . '<a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a> '
            . 'y reintentá (necesita prompt=consent).', false);
    }
    $email = '';
    try {
        $ui = arx_http('GET', 'https://www.googleapis.com/oauth2/v2/userinfo', [
            'headers' => ['Authorization: Bearer ' . $d['access_token']],
        ]);
        $uid = json_decode($ui['body'], true);
        $email = $uid['email'] ?? '';
    } catch (Exception $e) { /* no crítico */ }

    arx_oauth_save($email, $d['access_token'], $d['refresh_token'], $d['expires_in'] ?? 3600);
    arx_meta_set('connected_at', time());
    arx_page('Alibaba conectado',
        'Gmail <strong>' . htmlspecialchars($email ?: 'cuenta autorizada') . '</strong> conectado correctamente. '
        . 'El sistema empezará a leer los correos de Alibaba en el próximo poll.', true);
} catch (Exception $e) {
    arx_page('Error', htmlspecialchars($e->getMessage()), false);
}
