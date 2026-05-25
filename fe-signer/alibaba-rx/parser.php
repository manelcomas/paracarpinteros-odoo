<?php
/**
 * parser.php — detecta tipo de correo Alibaba, extrae número de pedido, proveedor,
 * comprador y estado a partir del subject/body/headers.
 *
 * Estados (con su prioridad — números mayores no se sobreescriben con menores):
 *   10 pendiente_pago
 *   15 pago_fallido
 *   20 esperando_pago
 *   25 pago_recibido
 *   28 pago_confirmacion_proveedor
 *   30 actualizado
 *   40 en_camino
 *   45 enviado
 *   50 cerca_entrega
 *   60 envio_completo
 *   65 entregado
 *   70 resena_pendiente
 *   80 cancelado
 *   85 revocado
 */

require_once __DIR__ . '/lib.php';

/**
 * Devuelve null si no es un correo relevante para el dashboard de pedidos.
 * Devuelve array con: tipo, numero, estado, estado_etiqueta, prioridad, comprador,
 * proveedor, monto, moneda, asunto.
 *
 * $msg: estructura full de Gmail message API (con payload, snippet, headers, labelIds).
 * $proveedor_labels: mapa opcional labelId → nombre del proveedor (de arx_load_proveedor_labels).
 *                    Si el correo lleva uno de esos labels, gana sobre el parsing del texto.
 */
function arx_parse_alibaba($msg, $proveedor_labels = []) {
    $headers = arx_headers_index($msg['payload']['headers'] ?? []);
    $subject = $headers['subject'] ?? ($msg['snippet'] ?? '');
    $from = $headers['from'] ?? '';
    $to = strtolower($headers['to'] ?? '');
    $cc = strtolower($headers['cc'] ?? '');
    $snippet = $msg['snippet'] ?? '';
    $ts = isset($msg['internalDate']) ? intval($msg['internalDate']) / 1000 : time();

    // Comprador por destinatario
    $comprador = 'desconocido';
    if (strpos($to, 'manelcomasbre@gmail.com') !== false) $comprador = 'manel';
    elseif (strpos($to, 'brenes_2110@hotmail.com') !== false || strpos($cc, 'gabriela@paracarpinteros.com') !== false) $comprador = 'gabriela';

    // Filtrar solo correos de Alibaba
    if (stripos($from, 'alibaba.com') === false) return null;

    // 1) Cotización
    if (stripos($subject, "You've received a new quotation") !== false ||
        stripos($subject, "You have a new quotation") !== false) {
        return [
            'tipo' => 'cotizacion',
            'asunto' => $subject, 'comprador' => $comprador, 'ts' => $ts,
        ];
    }

    // 2) Inquiry (mensaje de un proveedor potencial)
    if (stripos($subject, 'has sent you an inquiry') !== false ||
        stripos($subject, 'new inquiry') !== false) {
        return [
            'tipo' => 'inquiry', 'asunto' => $subject, 'comprador' => $comprador, 'ts' => $ts,
            'proveedor' => arx_extract_inquiry_supplier($subject),
        ];
    }

    // 3) Soporte
    if (stripos($from, 'buyer_help@service.alibaba.com') !== false ||
        stripos($subject, 'Response to your inquiry from Alibaba') !== false ||
        stripos($subject, 'Response to Your Inquiry from Alibaba') !== false) {
        return ['tipo' => 'soporte', 'asunto' => $subject, 'comprador' => $comprador, 'ts' => $ts];
    }

    // 4) Verificación de código (NO relevante para pedidos)
    if (preg_match('/es tu código de verificación/i', $subject) ||
        preg_match('/verification code/i', $subject)) {
        return null;
    }

    // 5) Notificación de pedido: necesita un número
    $numero = arx_extract_numero($subject);
    if (!$numero) {
        // Intentar también en el snippet por si el subject no lo trae
        $numero = arx_extract_numero($snippet);
    }
    if (!$numero) return null;

    // Detectar estado por patrones (orden importa: del más específico al más general)
    $patterns = [
        // [regex, estado, etiqueta, prioridad]
        ['/ha sido entregado/iu',                              'entregado',                  '✅ Entregado',         65],
        ['/Shipment has been completed/iu',                    'envio_completo',             '✅ Entregado',         60],
        ['/Puedes escribir una reseña/iu',                     'resena_pendiente',           '⭐ Reseña pendiente',  70],
        ['/Tu pedido está completo/iu',                        'resena_pendiente',           '⭐ Reseña pendiente',  70],
        ['/se ha cancelado/iu',                                'cancelado',                  '❌ Cancelado',         80],
        ['/han sido revocadas por el proveedor/iu',            'revocado',                   '❌ Cancelado',         85],
        ['/ha salido del depósito local/iu',                   'cerca_entrega',              '✈️ En camino',         50],
        ['/se ha enviado y está en camino/iu',                 'enviado',                    '✈️ En camino',         45],
        ['/Su pedido está en camino/iu',                       'en_camino',                  '✈️ En camino',         40],
        ['/ha sido actualizado por el proveedor/iu',           'actualizado',                '⚡ Acción',            30],
        ['/Pida a su proveedor que confirme el pago/iu',       'pago_confirmacion_proveedor','⚡ Acción',            28],
        ['/Su pago inicial ha sido recibido/iu',               'pago_recibido',              '⚡ Acción',            25],
        ['/Su pedido está esperando el pago/iu',               'esperando_pago',             '⚡ Acción',            20],
        ['/Su pago no fue exitoso/iu',                         'pago_fallido',               '⚡ Acción',            15],
        ['/El estado de pago de su pedido.*ha cambiado/iu',    'pago_cambio',                '⚡ Acción',            12],
        ['/está a la espera del pago inicial/iu',              'pendiente_pago',             '⚡ Acción',            10],
    ];

    // Resolución de proveedor: PRIMERO via labels Gmail (más fiable), luego via texto del snippet.
    $proveedor_por_label = arx_proveedor_from_labels($msg['labelIds'] ?? [], $proveedor_labels);

    foreach ($patterns as [$rx, $estado, $etiqueta, $prio]) {
        if (preg_match($rx, $subject) || preg_match($rx, $snippet)) {
            $proveedor = $proveedor_por_label ?: arx_extract_proveedor_from_snippet($snippet);
            $monto = arx_extract_monto_from_snippet($snippet);
            return [
                'tipo' => 'pedido',
                'numero' => $numero,
                'estado' => $estado,
                'estado_etiqueta' => $etiqueta,
                'prioridad' => $prio,
                'comprador' => $comprador,
                'proveedor' => $proveedor,
                'monto' => $monto['monto'] ?? null,
                'moneda' => $monto['moneda'] ?? null,
                'asunto' => $subject,
                'ts' => $ts,
            ];
        }
    }

    // Fallback: detectó número pero no estado conocido → pedido con estado desconocido
    return [
        'tipo' => 'pedido',
        'numero' => $numero,
        'estado' => 'desconocido',
        'estado_etiqueta' => '❓ Sin clasificar',
        'prioridad' => 0,
        'comprador' => $comprador,
        'proveedor' => $proveedor_por_label ?: arx_extract_proveedor_from_snippet($snippet),
        'asunto' => $subject,
        'ts' => $ts,
    ];
}

/**
 * Extrae los items (productos) de un correo Alibaba en formato HTML.
 * Patrón observado: <img alicdn 120x120/> ... <p bold> nombre </p> ... Cantidad: N UNIDAD ... Precio por unidad: MONEDA NUMERO
 * Devuelve array de items con keys: nombre, imagen_url, cantidad, unidad, precio_unitario, moneda.
 */
function arx_extract_items_from_html($html) {
    if (!$html) return [];
    $items = [];

    // Buscamos bloques: imagen alicdn 120x120 → bloque siguiente con nombre bold + cantidad + precio
    $pattern = '#<img[^>]*src="(https?://[^"]*alicdn\.com[^"]+)"[^>]*width="120"[^>]*height="120"[^>]*/?>'
             . '.*?<p[^>]*font-weight:\s*bold[^>]*>\s*(.+?)\s*</p>'
             . '(.*?)</tr>#si';

    if (!preg_match_all($pattern, $html, $matches, PREG_SET_ORDER)) {
        return [];
    }

    foreach ($matches as $m) {
        $imagen = trim($m[1]);
        // El nombre puede tener entidades HTML y tags incidentales
        $nombre = trim(html_entity_decode(strip_tags($m[2]), ENT_QUOTES | ENT_HTML5, 'UTF-8'));
        $nombre = preg_replace('/\s+/u', ' ', $nombre);
        if ($nombre === '') continue;

        $resto = $m[3];
        $item = ['nombre' => $nombre, 'imagen_url' => $imagen];

        // Cantidad: "Cantidad: 3 Pieces" o "Quantity: 3 Sets"
        if (preg_match('/(?:Cantidad|Quantity|数量)[:：]\s*([\d.,]+)\s*([A-Za-zÁÉÍÓÚáéíóúñÑ]+)/u', $resto, $cm)) {
            $item['cantidad'] = floatval(str_replace(',', '', $cm[1]));
            $item['unidad'] = trim($cm[2]);
        }

        // Precio por unidad: "Precio por unidad:USD 48.00 USD" o variantes
        // 1) MONEDA NUMERO (USD 48.00)
        if (preg_match('/(?:Precio por unidad|Unit Price|单价)[:：]?\s*([A-Z]{3})\s*([\d.,]+)/u', $resto, $pm)) {
            $item['moneda'] = $pm[1];
            $item['precio_unitario'] = floatval(str_replace(',', '', $pm[2]));
        }
        // 2) NUMERO MONEDA (48.00 USD)
        elseif (preg_match('/(?:Precio por unidad|Unit Price|单价)[:：]?\s*([\d.,]+)\s*([A-Z]{3})/u', $resto, $pm)) {
            $item['precio_unitario'] = floatval(str_replace(',', '', $pm[1]));
            $item['moneda'] = $pm[2];
        }

        $items[] = $item;
    }
    return $items;
}

/** Devuelve el nombre del proveedor si alguno de los labelIds del mensaje matchea el mapa. */
function arx_proveedor_from_labels($msg_label_ids, $proveedor_labels) {
    if (!is_array($msg_label_ids) || !$proveedor_labels) return null;
    foreach ($msg_label_ids as $lid) {
        if (isset($proveedor_labels[$lid])) return $proveedor_labels[$lid];
    }
    return null;
}

// ─── Helpers ────────────────────────────────────────────────────

function arx_headers_index($headers) {
    $out = [];
    foreach ($headers as $h) {
        $out[strtolower($h['name'])] = $h['value'];
    }
    return $out;
}

/** Extrae el número de Trade Assurance (típicamente 11-18 dígitos). */
function arx_extract_numero($text) {
    // Patrones específicos primero
    if (preg_match('/Trade Assurance(?: Order)?\s*(?:n\.?\s*°|No\.?|Number)?\s*[:#]?\s*(\d{10,20})/iu', $text, $m)) {
        return $m[1];
    }
    if (preg_match('/pedido\s+(\d{10,20})/iu', $text, $m)) {
        return $m[1];
    }
    if (preg_match('/\((\d{10,20})\)/u', $text, $m)) {
        return $m[1];
    }
    // Fallback genérico
    if (preg_match('/\b(\d{12,20})\b/u', $text, $m)) {
        return $m[1];
    }
    return null;
}

/** Extrae el nombre del proveedor del snippet ("El proveedor Mario Tang ha recibido..."). */
function arx_extract_proveedor_from_snippet($snippet) {
    if (preg_match('/[Ee]l proveedor\s+([A-Za-zÁÉÍÓÚáéíóúñÑ][A-Za-zÁÉÍÓÚáéíóúñÑ .\-]{1,40}?)\s+(?:ha\s+(?:recibido|enviado|revocado)|tangtong|liu|wang)/u', $snippet, $m)) {
        return trim($m[1]);
    }
    // Patrón "Hi Manuel Comas Hello,we" o "Hi <comprador>" no aplica para extraer proveedor.
    return null;
}

/** Extrae nombre del proveedor de un inquiry subject ("tong tang (United States) has sent you an inquiry"). */
function arx_extract_inquiry_supplier($subject) {
    if (preg_match('/inquiry:\s*([^(]+?)\s*\(/u', $subject, $m)) return trim($m[1]);
    if (preg_match('/^You have a new inquiry:\s*(.+?)\s+has sent/u', $subject, $m)) return trim($m[1]);
    return null;
}

/**
 * Intenta extraer monto del snippet. Los correos de pago suelen tener:
 *   "Se produjo un error en el pago de *** 77.00 con tarjeta..."
 * El "***" oculta la moneda (Alibaba no la muestra clara). Devolvemos solo el número.
 */
function arx_extract_monto_from_snippet($snippet) {
    if (preg_match('/pago de\s*\*+\s*([0-9.,]+)/u', $snippet, $m)) {
        return ['monto' => floatval(str_replace(',', '', $m[1])), 'moneda' => null];
    }
    if (preg_match('/(USD|EUR|CNY|MXN|CRC)\s*([0-9.,]+)/u', $snippet, $m)) {
        return ['monto' => floatval(str_replace(',', '', $m[2])), 'moneda' => $m[1]];
    }
    return ['monto' => null, 'moneda' => null];
}
