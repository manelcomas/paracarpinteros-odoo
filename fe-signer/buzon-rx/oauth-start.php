<?php
/** GET /buzon-rx/oauth-start — redirige a Google para autorizar. */
require_once __DIR__ . '/lib.php';

$state = bin2hex(random_bytes(16));
$st = bx_db()->prepare("INSERT INTO oauth_state(state,created_at) VALUES(?,?)");
$st->execute([$state, time()]);
// limpiar states viejos (>1h)
bx_db()->prepare("DELETE FROM oauth_state WHERE created_at < ?")->execute([time() - 3600]);

$params = http_build_query([
    'client_id' => bx_env('GOOGLE_OAUTH_CLIENT_ID'),
    'redirect_uri' => bx_env('GOOGLE_OAUTH_REDIRECT'),
    'response_type' => 'code',
    'scope' => 'https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.modify',
    'access_type' => 'offline',
    'prompt' => 'consent',
    'state' => $state,
]);
header('Location: https://accounts.google.com/o/oauth2/v2/auth?' . $params);
exit;
