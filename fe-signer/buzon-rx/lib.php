<?php
/**
 * Buzón Recepción FE — librería común.
 * OAuth Gmail + SQLite + AES-256-GCM. PHP nativo (sin composer).
 */

ini_set('display_errors', '0');
ini_set('log_errors', '1');
error_reporting(E_ALL & ~E_DEPRECATED & ~E_USER_DEPRECATED);

// ─── Configuración ───────────────────────────────────────────────
function bx_env($key, $default = '') {
    $v = getenv($key);
    return ($v === false || $v === '') ? $default : $v;
}

function bx_db_path() {
    return bx_env('BUZON_DB_PATH', '/var/www/html/buzon-rx/storage/buzon.db');
}

// ─── SQLite ──────────────────────────────────────────────────────
function bx_db() {
    static $pdo = null;
    if ($pdo !== null) return $pdo;
    $path = bx_db_path();
    $dir = dirname($path);
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    $pdo = new PDO('sqlite:' . $path);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec('PRAGMA journal_mode=WAL;');
    $pdo->exec('PRAGMA busy_timeout=5000;');
    bx_db_init($pdo);
    return $pdo;
}

function bx_db_init($pdo) {
    $pdo->exec("CREATE TABLE IF NOT EXISTS oauth_tokens (
        id INTEGER PRIMARY KEY CHECK(id=1),
        email TEXT,
        access_token TEXT,
        refresh_token_enc TEXT,
        expires_at INTEGER,
        updated_at INTEGER
    )");
    $pdo->exec("CREATE TABLE IF NOT EXISTS oauth_state (
        state TEXT PRIMARY KEY,
        created_at INTEGER
    )");
    $pdo->exec("CREATE TABLE IF NOT EXISTS xmls_recibidos (
        clave TEXT PRIMARY KEY,
        gmail_msg_id TEXT,
        fecha_recibido INTEGER,
        emisor_cedula TEXT, emisor_nombre TEXT,
        receptor_cedula TEXT,
        consecutivo TEXT,
        fecha_emision TEXT,
        monto_total REAL, moneda TEXT,
        xml_content BLOB,
        estado TEXT CHECK(estado IN ('pending','accepted','partial','rejected','expired','wrong_recipient','requires_manual','invalid')),
        mr_clave TEXT,
        mr_xml BLOB,
        mr_respuesta_hacienda BLOB,
        fecha_mr INTEGER,
        motivo_rechazo TEXT
    )");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_estado ON xmls_recibidos(estado)");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_fecha ON xmls_recibidos(fecha_recibido)");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_gmail ON xmls_recibidos(gmail_msg_id)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS consec_mr (
        tipo TEXT PRIMARY KEY CHECK(tipo IN ('05','06','07')),
        ultimo_consec INTEGER NOT NULL DEFAULT 0
    )");
    $pdo->exec("INSERT OR IGNORE INTO consec_mr VALUES ('05',0),('06',0),('07',0)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)");
}

function bx_meta_get($k, $default = null) {
    $st = bx_db()->prepare("SELECT v FROM meta WHERE k=?");
    $st->execute([$k]);
    $r = $st->fetchColumn();
    return ($r === false) ? $default : $r;
}
function bx_meta_set($k, $v) {
    $st = bx_db()->prepare("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v");
    $st->execute([$k, (string)$v]);
}

// ─── AES-256-GCM ─────────────────────────────────────────────────
function bx_enc_key() {
    // Key derivada de SIGNER_API_KEY + sal del .env. 32 bytes.
    $base = bx_env('SIGNER_API_KEY', 'fallback') . '|' . bx_env('OAUTH_ENC_KEY_DERIVE', 'fe-buzon-rx-v1');
    return hash('sha256', $base, true);
}
function bx_encrypt($plain) {
    $key = bx_enc_key();
    $iv = random_bytes(12);
    $tag = '';
    $ct = openssl_encrypt($plain, 'aes-256-gcm', $key, OPENSSL_RAW_DATA, $iv, $tag);
    if ($ct === false) throw new Exception('encrypt failed');
    return base64_encode($iv . $tag . $ct);
}
function bx_decrypt($b64) {
    $key = bx_enc_key();
    $raw = base64_decode($b64, true);
    if ($raw === false || strlen($raw) < 28) throw new Exception('cipher inválido');
    $iv = substr($raw, 0, 12);
    $tag = substr($raw, 12, 16);
    $ct = substr($raw, 28);
    $pt = openssl_decrypt($ct, 'aes-256-gcm', $key, OPENSSL_RAW_DATA, $iv, $tag);
    if ($pt === false) throw new Exception('decrypt failed');
    return $pt;
}

// ─── Respuestas HTTP ─────────────────────────────────────────────
function bx_json($data, $code = 200) {
    http_response_code($code);
    header('Content-Type: application/json');
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}
function bx_require_api_key() {
    $expected = bx_env('SIGNER_API_KEY');
    $got = $_SERVER['HTTP_X_API_KEY'] ?? '';
    if (!$expected || !hash_equals($expected, $got)) {
        bx_json(['error' => 'unauthorized'], 401);
    }
}

// ─── HTTP helper (curl) ──────────────────────────────────────────
function bx_http($method, $url, $opts = []) {
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
    curl_setopt($ch, CURLOPT_TIMEOUT, $opts['timeout'] ?? 30);
    curl_setopt($ch, CURLOPT_FOLLOWLOCATION, false);
    $headers = $opts['headers'] ?? [];
    if (isset($opts['body'])) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, $opts['body']);
    }
    if ($headers) curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    $body = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    if ($body === false) throw new Exception('curl: ' . $err);
    return ['code' => $code, 'body' => $body];
}

// ─── OAuth Google ────────────────────────────────────────────────
function bx_oauth_save($email, $access, $refresh, $expiresIn) {
    $pdo = bx_db();
    $expiresAt = time() + intval($expiresIn) - 60; // margen 1 min
    if ($refresh) {
        $st = $pdo->prepare("INSERT INTO oauth_tokens(id,email,access_token,refresh_token_enc,expires_at,updated_at)
            VALUES(1,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET email=excluded.email, access_token=excluded.access_token,
            refresh_token_enc=excluded.refresh_token_enc, expires_at=excluded.expires_at, updated_at=excluded.updated_at");
        $st->execute([$email, $access, bx_encrypt($refresh), $expiresAt, time()]);
    } else {
        // refresh sin nuevo refresh_token: solo actualizar access
        $st = $pdo->prepare("UPDATE oauth_tokens SET access_token=?, expires_at=?, updated_at=? WHERE id=1");
        $st->execute([$access, $expiresAt, time()]);
    }
}

function bx_oauth_row() {
    $st = bx_db()->query("SELECT * FROM oauth_tokens WHERE id=1");
    return $st->fetch(PDO::FETCH_ASSOC) ?: null;
}

/** Devuelve un access_token válido; refresca si caducó. Lanza Exception si no hay conexión. */
function bx_get_access_token() {
    $row = bx_oauth_row();
    if (!$row) throw new Exception('buzón no conectado (sin tokens)');
    if (intval($row['expires_at']) > time()) {
        return $row['access_token'];
    }
    // Refrescar
    $refresh = bx_decrypt($row['refresh_token_enc']);
    $resp = bx_http('POST', 'https://oauth2.googleapis.com/token', [
        'headers' => ['Content-Type: application/x-www-form-urlencoded'],
        'body' => http_build_query([
            'client_id' => bx_env('GOOGLE_OAUTH_CLIENT_ID'),
            'client_secret' => bx_env('GOOGLE_OAUTH_CLIENT_SECRET'),
            'refresh_token' => $refresh,
            'grant_type' => 'refresh_token',
        ]),
    ]);
    $d = json_decode($resp['body'], true);
    if ($resp['code'] !== 200 || empty($d['access_token'])) {
        throw new Exception('refresh falló: ' . substr($resp['body'], 0, 300));
    }
    bx_oauth_save($row['email'], $d['access_token'], $d['refresh_token'] ?? null, $d['expires_in'] ?? 3600);
    return $d['access_token'];
}
