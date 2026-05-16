<?php
/**
 * poll.php — descubre FE nuevas en el Gmail conectado.
 * Lo invoca el cron cada 10 min, o /buzon-rx/sync manualmente.
 * Devuelve (si se incluye) un array resumen; en CLI imprime el resumen.
 */
require_once __DIR__ . '/lib.php';

function bx_poll() {
    $resumen = ['nuevos' => 0, 'duplicados' => 0, 'errores' => [], 'mensajes' => 0];

    $token = bx_get_access_token();  // lanza Exception si no conectado
    $auth = ['Authorization: Bearer ' . $token];

    // 1) Listar mensajes con el filtro
    $query = bx_env('GMAIL_QUERY', 'to:envios@paracarpinteros.com has:attachment filename:xml newer_than:7d');
    $listResp = bx_http('GET',
        'https://gmail.googleapis.com/gmail/v1/users/me/messages?q=' . rawurlencode($query) . '&maxResults=50',
        ['headers' => $auth]);
    $list = json_decode($listResp['body'], true);
    if ($listResp['code'] !== 200) {
        throw new Exception('gmail list HTTP ' . $listResp['code'] . ': ' . substr($listResp['body'], 0, 200));
    }
    $messages = $list['messages'] ?? [];
    $resumen['mensajes'] = count($messages);

    // 2) Asegurar label "FE-procesada"
    $labelId = bx_ensure_label($auth, 'FE-procesada');

    $pdo = bx_db();
    foreach ($messages as $m) {
        $msgId = $m['id'];
        try {
            // ¿ya procesado este mensaje?
            $chk = $pdo->prepare("SELECT COUNT(*) FROM xmls_recibidos WHERE gmail_msg_id=?");
            $chk->execute([$msgId]);
            if ($chk->fetchColumn() > 0) { $resumen['duplicados']++; continue; }

            $msgResp = bx_http('GET',
                'https://gmail.googleapis.com/gmail/v1/users/me/messages/' . $msgId . '?format=full',
                ['headers' => $auth]);
            if ($msgResp['code'] !== 200) {
                $resumen['errores'][] = "msg $msgId: HTTP " . $msgResp['code'];
                continue;
            }
            $msg = json_decode($msgResp['body'], true);
            $xmls = bx_extract_xml_attachments($auth, $msgId, $msg['payload'] ?? []);
            if (!$xmls) continue;

            $algunoNuevo = false;
            foreach ($xmls as $xmlContent) {
                $parsed = bx_parse_fe_xml($xmlContent);
                if (!$parsed['clave']) {
                    $resumen['errores'][] = "msg $msgId: XML sin <Clave>";
                    continue;
                }
                // duplicado por clave
                $chk2 = $pdo->prepare("SELECT COUNT(*) FROM xmls_recibidos WHERE clave=?");
                $chk2->execute([$parsed['clave']]);
                if ($chk2->fetchColumn() > 0) { $resumen['duplicados']++; continue; }

                $estado = 'pending';
                $miCedula = bx_env('RECEPTOR_CEDULA', '304410837');
                if ($parsed['receptor_cedula'] && $parsed['receptor_cedula'] !== $miCedula) {
                    $estado = 'wrong_recipient';
                }
                $ins = $pdo->prepare("INSERT INTO xmls_recibidos
                    (clave,gmail_msg_id,fecha_recibido,emisor_cedula,emisor_nombre,receptor_cedula,
                     consecutivo,fecha_emision,monto_total,moneda,xml_content,estado)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)");
                $ins->execute([
                    $parsed['clave'], $msgId, time(),
                    $parsed['emisor_cedula'], $parsed['emisor_nombre'], $parsed['receptor_cedula'],
                    $parsed['consecutivo'], $parsed['fecha_emision'],
                    $parsed['monto_total'], $parsed['moneda'],
                    $xmlContent, $estado,
                ]);
                $resumen['nuevos']++;
                $algunoNuevo = true;
            }
            // Marcar el mensaje con el label
            if ($algunoNuevo && $labelId) {
                bx_http('POST',
                    'https://gmail.googleapis.com/gmail/v1/users/me/messages/' . $msgId . '/modify',
                    ['headers' => array_merge($auth, ['Content-Type: application/json']),
                     'body' => json_encode(['addLabelIds' => [$labelId]])]);
            }
        } catch (Exception $e) {
            $resumen['errores'][] = "msg $msgId: " . $e->getMessage();
        }
    }

    bx_meta_set('last_poll', time());
    return $resumen;
}

/** Asegura que existe el label dado; devuelve su id. */
function bx_ensure_label($auth, $name) {
    try {
        $r = bx_http('GET', 'https://gmail.googleapis.com/gmail/v1/users/me/labels', ['headers' => $auth]);
        $d = json_decode($r['body'], true);
        foreach (($d['labels'] ?? []) as $l) {
            if ($l['name'] === $name) return $l['id'];
        }
        // crear
        $c = bx_http('POST', 'https://gmail.googleapis.com/gmail/v1/users/me/labels',
            ['headers' => array_merge($auth, ['Content-Type: application/json']),
             'body' => json_encode(['name' => $name, 'labelListVisibility' => 'labelShow',
                                    'messageListVisibility' => 'show'])]);
        $cd = json_decode($c['body'], true);
        return $cd['id'] ?? null;
    } catch (Exception $e) {
        return null;
    }
}

/** Recorre el payload del mensaje y devuelve los contenidos XML (string). */
function bx_extract_xml_attachments($auth, $msgId, $payload) {
    $out = [];
    $walk = function ($part) use (&$walk, &$out, $auth, $msgId) {
        $filename = $part['filename'] ?? '';
        $mime = $part['mimeType'] ?? '';
        $isXml = (stripos($filename, '.xml') !== false) ||
                 (stripos($mime, 'xml') !== false && $filename !== '');
        if ($isXml) {
            $body = $part['body'] ?? [];
            if (!empty($body['attachmentId'])) {
                $a = bx_http('GET',
                    'https://gmail.googleapis.com/gmail/v1/users/me/messages/' . $msgId .
                    '/attachments/' . $body['attachmentId'], ['headers' => $auth]);
                $ad = json_decode($a['body'], true);
                if (!empty($ad['data'])) {
                    $out[] = bx_b64url_decode($ad['data']);
                }
            } elseif (!empty($body['data'])) {
                $out[] = bx_b64url_decode($body['data']);
            }
        }
        foreach (($part['parts'] ?? []) as $sub) $walk($sub);
    };
    $walk($payload);
    return $out;
}

function bx_b64url_decode($s) {
    return base64_decode(strtr($s, '-_', '+/'));
}

/**
 * Parser básico de la FE (Bloque 3 ampliará con validación XSD).
 * Extrae los campos clave para la tabla.
 */
function bx_parse_fe_xml($xml) {
    $r = ['clave'=>null,'consecutivo'=>null,'fecha_emision'=>null,
          'emisor_cedula'=>null,'emisor_nombre'=>null,'receptor_cedula'=>null,
          'monto_total'=>0,'moneda'=>'CRC'];
    if (preg_match('/<Clave>(\d+)<\/Clave>/', $xml, $m)) $r['clave'] = $m[1];
    if (preg_match('/<NumeroConsecutivo>([^<]+)<\/NumeroConsecutivo>/', $xml, $m)) $r['consecutivo'] = trim($m[1]);
    if (preg_match('/<FechaEmision>([^<]+)<\/FechaEmision>/', $xml, $m)) $r['fecha_emision'] = trim($m[1]);
    if (preg_match('/<Emisor>.*?<Nombre>([^<]+)<\/Nombre>/s', $xml, $m)) $r['emisor_nombre'] = trim($m[1]);
    if (preg_match('/<Emisor>.*?<Identificacion>.*?<Numero>([^<]+)<\/Numero>/s', $xml, $m)) $r['emisor_cedula'] = trim($m[1]);
    if (preg_match('/<Receptor>.*?<Identificacion>.*?<Numero>([^<]+)<\/Numero>/s', $xml, $m)) $r['receptor_cedula'] = trim($m[1]);
    if (preg_match('/<TotalComprobante>([\d.]+)<\/TotalComprobante>/', $xml, $m)) $r['monto_total'] = (float)$m[1];
    if (preg_match('/<CodigoMoneda>([^<]+)<\/CodigoMoneda>/', $xml, $m)) $r['moneda'] = trim($m[1]);
    return $r;
}

// ─── Si se ejecuta directamente (cron / CLI) ─────────────────────
if (php_sapi_name() === 'cli' || (isset($_SERVER['SCRIPT_FILENAME']) &&
    realpath($_SERVER['SCRIPT_FILENAME']) === realpath(__FILE__) && php_sapi_name() !== 'cli')) {
    try {
        $res = bx_poll();
        $line = '[' . gmdate('Y-m-d H:i:s') . ' UTC] poll: ' . json_encode($res, JSON_UNESCAPED_UNICODE);
        if (php_sapi_name() === 'cli') {
            echo $line . "\n";
        } else {
            bx_json($res);
        }
    } catch (Exception $e) {
        $line = '[' . gmdate('Y-m-d H:i:s') . ' UTC] poll ERROR: ' . $e->getMessage();
        if (php_sapi_name() === 'cli') { fwrite(STDERR, $line . "\n"); exit(1); }
        bx_json(['error' => $e->getMessage()], 500);
    }
}
