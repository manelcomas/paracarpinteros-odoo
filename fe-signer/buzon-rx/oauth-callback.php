<?php
/** GET /buzon-rx/oauth-callback?code=...&state=... — intercambia code por tokens. */
require_once __DIR__ . '/lib.php';

function bx_page($title, $msg, $ok = true) {
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
       . '<p style="color:#999;font-size:.8rem;margin-top:20px;">Podés cerrar esta ventana y volver al conversor.</p>'
       . '</div></body></html>';
    exit;
}

$code = $_GET['code'] ?? '';
$state = $_GET['state'] ?? '';
$error = $_GET['error'] ?? '';

if ($error) bx_page('Autorización cancelada', 'Google devolvió: ' . htmlspecialchars($error), false);
if (!$code || !$state) bx_page('Faltan parámetros', 'No se recibió code o state de Google.', false);

// Validar state
$st = bx_db()->prepare("SELECT state FROM oauth_state WHERE state=?");
$st->execute([$state]);
if (!$st->fetchColumn()) bx_page('State inválido', 'El parámetro state no coincide (posible CSRF o expirado).', false);
bx_db()->prepare("DELETE FROM oauth_state WHERE state=?")->execute([$state]);

try {
    // Intercambiar code por tokens
    $resp = bx_http('POST', 'https://oauth2.googleapis.com/token', [
        'headers' => ['Content-Type: application/x-www-form-urlencoded'],
        'body' => http_build_query([
            'client_id' => bx_env('GOOGLE_OAUTH_CLIENT_ID'),
            'client_secret' => bx_env('GOOGLE_OAUTH_CLIENT_SECRET'),
            'code' => $code,
            'redirect_uri' => bx_env('GOOGLE_OAUTH_REDIRECT'),
            'grant_type' => 'authorization_code',
        ]),
    ]);
    $d = json_decode($resp['body'], true);
    if ($resp['code'] !== 200 || empty($d['access_token'])) {
        bx_page('Error al obtener tokens', 'Google: ' . htmlspecialchars(substr($resp['body'], 0, 300)), false);
    }
    if (empty($d['refresh_token'])) {
        bx_page('Sin refresh_token', 'Google no devolvió refresh_token. Revoca el acceso en myaccount.google.com/permissions y reintentá (necesita prompt=consent).', false);
    }
    // Obtener email del usuario
    $email = '';
    try {
        $ui = bx_http('GET', 'https://www.googleapis.com/oauth2/v2/userinfo', [
            'headers' => ['Authorization: Bearer ' . $d['access_token']],
        ]);
        $uid = json_decode($ui['body'], true);
        $email = $uid['email'] ?? '';
    } catch (Exception $e) { /* no crítico */ }

    bx_oauth_save($email, $d['access_token'], $d['refresh_token'], $d['expires_in'] ?? 3600);
    bx_meta_set('connected_at', time());
    bx_page('Buzón conectado', 'Gmail <strong>' . htmlspecialchars($email ?: 'cuenta autorizada') . '</strong> conectado correctamente. El sistema empezará a leer las FE recibidas.', true);
} catch (Exception $e) {
    bx_page('Error', htmlspecialchars($e->getMessage()), false);
}
