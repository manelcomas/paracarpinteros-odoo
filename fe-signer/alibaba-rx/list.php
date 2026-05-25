<?php
/**
 * GET /alibaba-rx/list — devuelve pedidos JSON.
 * Query params (todos opcionales):
 *   estado=en_camino,entregado,...      filtro por estado (CSV)
 *   comprador=manel|gabriela
 *   proveedor=<substring>               filtro LIKE
 *   shenzhen=0|1                        filtro por in_shenzhen
 *   group=SH-01                         filtro por shenzhen_group
 *   limit=200                           default 500
 *   offset=0
 *   exclude_terminal=1                  excluye cancelado/revocado/entregado/resena
 *   order=desc|asc                      por fecha_ultimo_evento (default desc)
 */
require_once __DIR__ . '/lib.php';
arx_require_api_key();

$pdo = arx_db();
$where = [];
$args = [];

if (!empty($_GET['estado'])) {
    $estados = explode(',', $_GET['estado']);
    $placeholders = implode(',', array_fill(0, count($estados), '?'));
    $where[] = "estado IN ($placeholders)";
    foreach ($estados as $e) $args[] = trim($e);
}
if (!empty($_GET['comprador'])) {
    $where[] = "comprador = ?";
    $args[] = $_GET['comprador'];
}
if (!empty($_GET['proveedor'])) {
    $where[] = "proveedor LIKE ?";
    $args[] = '%' . $_GET['proveedor'] . '%';
}
if (isset($_GET['shenzhen'])) {
    $where[] = "in_shenzhen = ?";
    $args[] = intval($_GET['shenzhen']);
}
if (!empty($_GET['group'])) {
    $where[] = "shenzhen_group = ?";
    $args[] = $_GET['group'];
}
if (!empty($_GET['exclude_terminal'])) {
    $where[] = "estado NOT IN ('cancelado','revocado','entregado','envio_completo','resena_pendiente')";
}

$sql = "SELECT * FROM pedidos";
if ($where) $sql .= " WHERE " . implode(' AND ', $where);
$order = (($_GET['order'] ?? 'desc') === 'asc') ? 'ASC' : 'DESC';
$sql .= " ORDER BY fecha_ultimo_evento $order";

$limit = min(intval($_GET['limit'] ?? 500), 2000);
$offset = max(intval($_GET['offset'] ?? 0), 0);
$sql .= " LIMIT $limit OFFSET $offset";

$st = $pdo->prepare($sql);
$st->execute($args);
$rows = $st->fetchAll(PDO::FETCH_ASSOC);

// Cast tipos + adjuntar items por pedido
$numeros = array_column($rows, 'numero');
$items_por_numero = [];
if ($numeros) {
    $placeholders = implode(',', array_fill(0, count($numeros), '?'));
    $sti = $pdo->prepare("SELECT numero, nombre, imagen_url, cantidad, unidad, precio_unitario, moneda
                          FROM pedido_items WHERE numero IN ($placeholders) ORDER BY id");
    $sti->execute($numeros);
    foreach ($sti->fetchAll(PDO::FETCH_ASSOC) as $it) {
        $num = $it['numero'];
        unset($it['numero']);
        $it['cantidad'] = $it['cantidad'] !== null ? floatval($it['cantidad']) : null;
        $it['precio_unitario'] = $it['precio_unitario'] !== null ? floatval($it['precio_unitario']) : null;
        $items_por_numero[$num][] = $it;
    }
}

foreach ($rows as &$r) {
    $r['prioridad'] = intval($r['prioridad']);
    $r['fecha_primer_evento'] = intval($r['fecha_primer_evento']);
    $r['fecha_ultimo_evento'] = intval($r['fecha_ultimo_evento']);
    $r['in_shenzhen'] = intval($r['in_shenzhen']);
    $r['monto'] = $r['monto'] !== null ? floatval($r['monto']) : null;
    $r['created_at'] = intval($r['created_at']);
    $r['updated_at'] = intval($r['updated_at']);
    $r['items'] = $items_por_numero[$r['numero']] ?? [];
}

// Resumen de grupos Shenzhen
$grupos = [];
foreach ($pdo->query("SELECT * FROM envios_shenzhen ORDER BY fecha_creacion DESC") as $g) {
    $grupos[] = [
        'grupo' => $g['grupo'],
        'estado' => $g['estado'],
        'fecha_creacion' => intval($g['fecha_creacion']),
        'fecha_envio' => intval($g['fecha_envio']),
        'notas' => $g['notas'],
    ];
}

arx_json([
    'pedidos' => $rows,
    'total' => count($rows),
    'shenzhen_grupos' => $grupos,
]);
