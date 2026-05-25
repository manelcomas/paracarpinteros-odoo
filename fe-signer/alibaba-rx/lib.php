<?php
/**
 * alibaba-rx — librería común.
 * OAuth Gmail + SQLite + AES-256-GCM. PHP nativo (sin composer).
 * Replica el patrón de buzon-rx con prefijo arx_ y BD propia (alibaba.db).
 */

ini_set('display_errors', '0');
ini_set('log_errors', '1');
error_reporting(E_ALL & ~E_DEPRECATED & ~E_USER_DEPRECATED);

// ─── CORS ────────────────────────────────────────────────────────
if (!headers_sent()) {
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
    header('Access-Control-Max-Age: 86400');
}

// ─── Configuración ───────────────────────────────────────────────
function arx_env($key, $default = '') {
    $v = getenv($key);
    return ($v === false || $v === '') ? $default : $v;
}

function arx_db_path() {
    return arx_env('ALIBABA_DB_PATH', '/var/www/html/alibaba-rx/storage/alibaba.db');
}

// ─── SQLite ──────────────────────────────────────────────────────
function arx_db() {
    static $pdo = null;
    if ($pdo !== null) return $pdo;
    $path = arx_db_path();
    $dir = dirname($path);
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    $pdo = new PDO('sqlite:' . $path);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec('PRAGMA journal_mode=WAL;');
    $pdo->exec('PRAGMA busy_timeout=5000;');
    arx_db_init($pdo);
    return $pdo;
}

function arx_db_init($pdo) {
    // OAuth (mismo patrón que buzon-rx)
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

    // Estado vigente por pedido (uno mata al otro: UPSERT por numero)
    $pdo->exec("CREATE TABLE IF NOT EXISTS pedidos (
        numero TEXT PRIMARY KEY,
        comprador TEXT,                       -- 'manel' | 'gabriela' | 'desconocido'
        proveedor TEXT,                       -- 'Mario Tang', ...
        estado TEXT,                          -- pendiente_pago | en_camino | entregado | ...
        estado_etiqueta TEXT,                 -- '⚡ Acción' | '✈️ En camino' | ...
        prioridad INTEGER DEFAULT 0,          -- ranking del estado (para no retroceder por correo tardío)
        monto REAL,                           -- nullable
        moneda TEXT,                          -- nullable
        fecha_primer_evento INTEGER,          -- ts del primer correo conocido
        fecha_ultimo_evento INTEGER,          -- ts del último correo conocido (drives el estado actual)
        asunto_ultimo TEXT,
        gmail_msg_id_ultimo TEXT,
        in_shenzhen INTEGER DEFAULT 0,        -- 0/1 flag consolidación
        shenzhen_group TEXT,                  -- 'SH-01' agrupador opcional
        notas TEXT,                           -- anotaciones manuales
        created_at INTEGER, updated_at INTEGER
    )");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_estado ON pedidos(estado)");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_comprador ON pedidos(comprador)");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_proveedor ON pedidos(proveedor)");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_shenzhen ON pedidos(in_shenzhen)");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_fecha ON pedidos(fecha_ultimo_evento)");

    // Historial de eventos (uno por correo procesado — auditoría)
    $pdo->exec("CREATE TABLE IF NOT EXISTS eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero TEXT,
        gmail_msg_id TEXT UNIQUE,
        estado_nuevo TEXT,
        estado_etiqueta TEXT,
        asunto TEXT,
        ts INTEGER
    )");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_eventos_numero ON eventos(numero)");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_eventos_ts ON eventos(ts)");

    // Grupos de envío Shenzhen (cuando se marca consolidación, los pedidos se agrupan)
    $pdo->exec("CREATE TABLE IF NOT EXISTS envios_shenzhen (
        grupo TEXT PRIMARY KEY,               -- 'SH-01'
        estado TEXT,                          -- agrupando | enviado_cr | aduana | recibido
        fecha_creacion INTEGER,
        fecha_envio INTEGER,
        notas TEXT
    )");

    // Items (productos) del pedido — extraídos del body HTML cuando el correo los trae
    $pdo->exec("CREATE TABLE IF NOT EXISTS pedido_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero TEXT NOT NULL,
        nombre TEXT,
        imagen_url TEXT,
        cantidad REAL,
        unidad TEXT,
        precio_unitario REAL,
        moneda TEXT,
        source_msg_id TEXT,
        UNIQUE(numero, nombre)
    )");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_items_numero ON pedido_items(numero)");

    // Cotizaciones e inquiries (no son pedidos pero los registramos por completitud)
    $pdo->exec("CREATE TABLE IF NOT EXISTS contactos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT,                            -- cotizacion | inquiry | soporte
        proveedor TEXT,
        asunto TEXT,
        gmail_msg_id TEXT UNIQUE,
        ts INTEGER
    )");

    $pdo->exec("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)");
}

function arx_meta_get($k, $default = null) {
    $st = arx_db()->prepare("SELECT v FROM meta WHERE k=?");
    $st->execute([$k]);
    $r = $st->fetchColumn();
    return ($r === false) ? $default : $r;
}
function arx_meta_set($k, $v) {
    $st = arx_db()->prepare("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v");
    $st->execute([$k, (string)$v]);
}

// ─── AES-256-GCM ─────────────────────────────────────────────────
function arx_enc_key() {
    // Clave derivada propia del módulo alibaba-rx — independiente de buzon-rx.
    $base = arx_env('SIGNER_API_KEY', 'fallback') . '|' . arx_env('ALIBABA_OAUTH_ENC_KEY_DERIVE', 'fe-alibaba-rx-v1');
    return hash('sha256', $base, true);
}
function arx_encrypt($plain) {
    $key = arx_enc_key();
    $iv = random_bytes(12);
    $tag = '';
    $ct = openssl_encrypt($plain, 'aes-256-gcm', $key, OPENSSL_RAW_DATA, $iv, $tag);
    if ($ct === false) throw new Exception('encrypt failed');
    return base64_encode($iv . $tag . $ct);
}
function arx_decrypt($b64) {
    $key = arx_enc_key();
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
function arx_json($data, $code = 200) {
    http_response_code($code);
    header('Content-Type: application/json');
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}
function arx_require_api_key() {
    $expected = arx_env('SIGNER_API_KEY');
    $got = $_SERVER['HTTP_X_API_KEY'] ?? '';
    if (!$expected || !hash_equals($expected, $got)) {
        arx_json(['error' => 'unauthorized'], 401);
    }
}

// ─── HTTP helper (curl) ──────────────────────────────────────────
function arx_http($method, $url, $opts = []) {
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
// El redirect URI para alibaba-rx se lee de ALIBABA_OAUTH_REDIRECT (si está
// configurado) con fallback a GOOGLE_OAUTH_REDIRECT_ALIBABA. Hay que añadir
// este URI al OAuth client en Google Cloud Console:
//   https://panel.paracarpinteros.com/alibaba-rx/oauth-callback
// El client_id/secret se REUSAN del módulo buzon-rx (GOOGLE_OAUTH_CLIENT_ID/SECRET).
function arx_oauth_redirect() {
    return arx_env('ALIBABA_OAUTH_REDIRECT',
           arx_env('GOOGLE_OAUTH_REDIRECT_ALIBABA',
                   'https://panel.paracarpinteros.com/alibaba-rx/oauth-callback'));
}

function arx_oauth_save($email, $access, $refresh, $expiresIn) {
    $pdo = arx_db();
    $expiresAt = time() + intval($expiresIn) - 60; // margen 1 min
    if ($refresh) {
        $st = $pdo->prepare("INSERT INTO oauth_tokens(id,email,access_token,refresh_token_enc,expires_at,updated_at)
            VALUES(1,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET email=excluded.email, access_token=excluded.access_token,
            refresh_token_enc=excluded.refresh_token_enc, expires_at=excluded.expires_at, updated_at=excluded.updated_at");
        $st->execute([$email, $access, arx_encrypt($refresh), $expiresAt, time()]);
    } else {
        $st = $pdo->prepare("UPDATE oauth_tokens SET access_token=?, expires_at=?, updated_at=? WHERE id=1");
        $st->execute([$access, $expiresAt, time()]);
    }
}

function arx_oauth_row() {
    $st = arx_db()->query("SELECT * FROM oauth_tokens WHERE id=1");
    return $st->fetch(PDO::FETCH_ASSOC) ?: null;
}

/**
 * Carga las labels de Gmail y devuelve un mapa labelId → nombre del proveedor
 * para los labels que sigan el patrón "🏭 Alibaba/Proveedores/<nombre>".
 * Cachea el resultado en static por la duración del proceso.
 */
function arx_load_proveedor_labels($auth) {
    static $cache = null;
    if ($cache !== null) return $cache;
    $cache = [];
    try {
        $r = arx_http('GET', 'https://gmail.googleapis.com/gmail/v1/users/me/labels', ['headers' => $auth]);
        if ($r['code'] !== 200) return $cache;
        $d = json_decode($r['body'], true);
        $prefix = '🏭 Alibaba/Proveedores/';
        $prefixLen = strlen($prefix);
        foreach ($d['labels'] ?? [] as $l) {
            $name = $l['name'] ?? '';
            if (strpos($name, $prefix) === 0) {
                $cache[$l['id']] = trim(substr($name, $prefixLen));
            }
        }
    } catch (Exception $e) { /* sin labels disponibles */ }
    return $cache;
}

/** Devuelve un access_token válido; refresca si caducó. Lanza Exception si no hay conexión. */
function arx_get_access_token() {
    $row = arx_oauth_row();
    if (!$row) throw new Exception('alibaba-rx no conectado (sin tokens)');
    if (intval($row['expires_at']) > time()) {
        return $row['access_token'];
    }
    $refresh = arx_decrypt($row['refresh_token_enc']);
    $resp = arx_http('POST', 'https://oauth2.googleapis.com/token', [
        'headers' => ['Content-Type: application/x-www-form-urlencoded'],
        'body' => http_build_query([
            'client_id' => arx_env('GOOGLE_OAUTH_CLIENT_ID'),
            'client_secret' => arx_env('GOOGLE_OAUTH_CLIENT_SECRET'),
            'refresh_token' => $refresh,
            'grant_type' => 'refresh_token',
        ]),
    ]);
    $d = json_decode($resp['body'], true);
    if ($resp['code'] !== 200 || empty($d['access_token'])) {
        throw new Exception('refresh falló: ' . substr($resp['body'], 0, 300));
    }
    arx_oauth_save($row['email'], $d['access_token'], $d['refresh_token'] ?? null, $d['expires_in'] ?? 3600);
    return $d['access_token'];
}

// ─── UPSERT de pedidos (uno mata al otro) ────────────────────────
/**
 * Aplica un evento a un pedido. Si el estado nuevo tiene prioridad mayor o igual al actual,
 * lo sobreescribe. Si tiene prioridad menor (correo tardío de un estado anterior), solo se
 * registra en eventos pero no toca el estado vigente.
 */
function arx_upsert_pedido($numero, $estado, $estado_etiqueta, $prioridad, $extra = []) {
    $pdo = arx_db();
    $now = time();
    $ts = $extra['ts'] ?? $now;

    $st = $pdo->prepare("SELECT prioridad, fecha_ultimo_evento FROM pedidos WHERE numero=?");
    $st->execute([$numero]);
    $cur = $st->fetch(PDO::FETCH_ASSOC);

    if (!$cur) {
        // INSERT
        $ins = $pdo->prepare("INSERT INTO pedidos
            (numero, comprador, proveedor, estado, estado_etiqueta, prioridad, monto, moneda,
             fecha_primer_evento, fecha_ultimo_evento, asunto_ultimo, gmail_msg_id_ultimo,
             created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)");
        $ins->execute([
            $numero,
            $extra['comprador'] ?? 'desconocido',
            $extra['proveedor'] ?? null,
            $estado, $estado_etiqueta, $prioridad,
            $extra['monto'] ?? null,
            $extra['moneda'] ?? null,
            $ts, $ts,
            $extra['asunto'] ?? null,
            $extra['gmail_msg_id'] ?? null,
            $now, $now
        ]);
        return 'inserted';
    }

    // Solo overwrite si la prioridad es >= actual (correo nuevo gana, pero respetamos eventos en orden)
    if ($prioridad >= intval($cur['prioridad']) || $ts > intval($cur['fecha_ultimo_evento'])) {
        $sets = ['estado=?', 'estado_etiqueta=?', 'prioridad=?', 'fecha_ultimo_evento=?',
                 'asunto_ultimo=?', 'gmail_msg_id_ultimo=?', 'updated_at=?'];
        $vals = [$estado, $estado_etiqueta, $prioridad, $ts,
                 $extra['asunto'] ?? null, $extra['gmail_msg_id'] ?? null, $now];
        // Proveedor/comprador: solo se actualiza si llega valor (no NULL)
        if (!empty($extra['proveedor'])) { $sets[] = 'proveedor=?'; $vals[] = $extra['proveedor']; }
        if (!empty($extra['comprador']) && $extra['comprador'] !== 'desconocido') {
            $sets[] = 'comprador=?'; $vals[] = $extra['comprador'];
        }
        if (!empty($extra['monto'])) { $sets[] = 'monto=?'; $vals[] = $extra['monto']; }
        if (!empty($extra['moneda'])) { $sets[] = 'moneda=?'; $vals[] = $extra['moneda']; }
        $vals[] = $numero;
        $sql = "UPDATE pedidos SET " . implode(',', $sets) . " WHERE numero=?";
        $pdo->prepare($sql)->execute($vals);
        return 'updated';
    }
    return 'skipped';
}

/**
 * Guarda los items de un pedido (UPSERT por numero+nombre). $items es un array de arrays con
 * keys: nombre, imagen_url, cantidad, unidad, precio_unitario, moneda.
 */
function arx_save_items($numero, $items, $source_msg_id) {
    if (!$items) return 0;
    $pdo = arx_db();
    $st = $pdo->prepare("INSERT INTO pedido_items
        (numero, nombre, imagen_url, cantidad, unidad, precio_unitario, moneda, source_msg_id)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(numero, nombre) DO UPDATE SET
          imagen_url = COALESCE(excluded.imagen_url, imagen_url),
          cantidad = COALESCE(excluded.cantidad, cantidad),
          unidad = COALESCE(excluded.unidad, unidad),
          precio_unitario = COALESCE(excluded.precio_unitario, precio_unitario),
          moneda = COALESCE(excluded.moneda, moneda),
          source_msg_id = excluded.source_msg_id");
    $count = 0;
    foreach ($items as $it) {
        if (empty($it['nombre'])) continue;
        $st->execute([
            $numero, $it['nombre'], $it['imagen_url'] ?? null,
            $it['cantidad'] ?? null, $it['unidad'] ?? null,
            $it['precio_unitario'] ?? null, $it['moneda'] ?? null,
            $source_msg_id
        ]);
        $count++;
    }
    return $count;
}

/** Registra un evento (auditoría). Devuelve true si era nuevo, false si ya existía. */
function arx_log_evento($numero, $gmail_msg_id, $estado, $estado_etiqueta, $asunto, $ts) {
    try {
        $st = arx_db()->prepare("INSERT INTO eventos(numero,gmail_msg_id,estado_nuevo,estado_etiqueta,asunto,ts)
            VALUES(?,?,?,?,?,?)");
        $st->execute([$numero, $gmail_msg_id, $estado, $estado_etiqueta, $asunto, $ts]);
        return true;
    } catch (PDOException $e) {
        // UNIQUE constraint on gmail_msg_id → ya procesado
        if (strpos($e->getMessage(), 'UNIQUE') !== false) return false;
        throw $e;
    }
}
