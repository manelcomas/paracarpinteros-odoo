<?php
/**
 * GET /alibaba-rx/oauth-start — redirige a Google para autorizar la cuenta Gmail
 * que recibe los correos de Alibaba (manelcomasbre@gmail.com).
 */
require_once __DIR__ . '/lib.php';

$state = bin2hex(random_bytes(16));
$st = arx_db()->prepare("INSERT INTO oauth_state(state,created_at) VALUES(?,?)");
$st->execute([$state, time()]);
// limpiar states viejos (>1h)
arx_db()->prepare("DELETE FROM oauth_state WHERE created_at < ?")->execute([time() - 3600]);

$params = http_build_query([
    'client_id' => arx_env('GOOGLE_OAUTH_CLIENT_ID'),
    'redirect_uri' => arx_oauth_redirect(),
    'response_type' => 'code',
    // Read + modify (modify nos permite añadir etiquetas Gmail desde el poll si queremos)
    'scope' => 'https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.modify',
    'access_type' => 'offline',
    'prompt' => 'consent',
    'state' => $state,
    'login_hint' => 'manelcomasbre@gmail.com',
]);
header('Location: https://accounts.google.com/o/oauth2/v2/auth?' . $params);
exit;
