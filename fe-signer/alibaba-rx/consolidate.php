<?php
/**
 * POST /alibaba-rx/consolidate — gestiona la marca "consolidar Shenzhen" y
 * los grupos de envío. Acciones:
 *
 *   action=toggle    {numero: "...", in_shenzhen: 0|1, shenzhen_group?: "SH-01"}
 *                    → marca/desmarca un pedido. Opcional asigna grupo.
 *
 *   action=group_create  {grupo: "SH-01", notas?: ""}
 *                    → crea un grupo de envío.
 *
 *   action=group_update  {grupo: "SH-01", estado?: "...", notas?: "...", fecha_envio?: ts}
 *                    → actualiza estado/datos del grupo.
 *
 *   action=group_assign  {grupo: "SH-01", numeros: ["12345...","67890..."]}
 *                    → asigna varios pedidos al grupo, marca in_shenzhen=1.
 *
 *   action=note          {numero: "...", notas: "..."}
 *                    → edita la nota libre del pedido.
 */
require_once __DIR__ . '/lib.php';

arx_require_api_key();
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') { http_response_code(204); exit; }
if ($_SERVER['REQUEST_METHOD'] !== 'POST') arx_json(['error' => 'method not allowed'], 405);

$raw = file_get_contents('php://input');
$in = json_decode($raw, true);
if (!is_array($in)) arx_json(['error' => 'invalid json body'], 400);

$action = $in['action'] ?? '';
$pdo = arx_db();
$now = time();

try {
    switch ($action) {
        case 'toggle': {
            $numero = $in['numero'] ?? '';
            if (!$numero) arx_json(['error' => 'numero requerido'], 400);
            $flag = intval($in['in_shenzhen'] ?? 0) ? 1 : 0;
            $grupo = $in['shenzhen_group'] ?? null;
            $sets = ['in_shenzhen=?', 'updated_at=?'];
            $vals = [$flag, $now];
            if ($grupo !== null) { $sets[] = 'shenzhen_group=?'; $vals[] = $grupo; }
            $vals[] = $numero;
            $sql = "UPDATE pedidos SET " . implode(',', $sets) . " WHERE numero=?";
            $st = $pdo->prepare($sql);
            $st->execute($vals);
            if ($st->rowCount() === 0) arx_json(['error' => 'pedido no existe'], 404);
            arx_json(['ok' => true, 'numero' => $numero, 'in_shenzhen' => $flag, 'shenzhen_group' => $grupo]);
        }
        case 'group_create': {
            $grupo = $in['grupo'] ?? '';
            if (!$grupo) arx_json(['error' => 'grupo requerido'], 400);
            $st = $pdo->prepare("INSERT INTO envios_shenzhen(grupo,estado,fecha_creacion,notas)
                VALUES(?,?,?,?) ON CONFLICT(grupo) DO NOTHING");
            $st->execute([$grupo, $in['estado'] ?? 'agrupando', $now, $in['notas'] ?? null]);
            arx_json(['ok' => true, 'grupo' => $grupo]);
        }
        case 'group_update': {
            $grupo = $in['grupo'] ?? '';
            if (!$grupo) arx_json(['error' => 'grupo requerido'], 400);
            $sets = []; $vals = [];
            foreach (['estado','notas'] as $k) {
                if (array_key_exists($k, $in)) { $sets[] = "$k=?"; $vals[] = $in[$k]; }
            }
            if (array_key_exists('fecha_envio', $in)) {
                $sets[] = 'fecha_envio=?'; $vals[] = intval($in['fecha_envio']);
            }
            if (!$sets) arx_json(['error' => 'nada que actualizar'], 400);
            $vals[] = $grupo;
            $st = $pdo->prepare("UPDATE envios_shenzhen SET " . implode(',', $sets) . " WHERE grupo=?");
            $st->execute($vals);
            if ($st->rowCount() === 0) arx_json(['error' => 'grupo no existe'], 404);
            arx_json(['ok' => true, 'grupo' => $grupo]);
        }
        case 'group_assign': {
            $grupo = $in['grupo'] ?? '';
            $numeros = $in['numeros'] ?? [];
            if (!$grupo || !is_array($numeros) || !$numeros) arx_json(['error' => 'grupo y numeros[] requeridos'], 400);
            $pdo->beginTransaction();
            $st = $pdo->prepare("INSERT INTO envios_shenzhen(grupo,estado,fecha_creacion)
                VALUES(?,?,?) ON CONFLICT(grupo) DO NOTHING");
            $st->execute([$grupo, 'agrupando', $now]);
            $up = $pdo->prepare("UPDATE pedidos SET in_shenzhen=1, shenzhen_group=?, updated_at=? WHERE numero=?");
            $ok = 0;
            foreach ($numeros as $n) {
                $up->execute([$grupo, $now, $n]);
                $ok += $up->rowCount();
            }
            $pdo->commit();
            arx_json(['ok' => true, 'grupo' => $grupo, 'asignados' => $ok]);
        }
        case 'note': {
            $numero = $in['numero'] ?? '';
            if (!$numero) arx_json(['error' => 'numero requerido'], 400);
            $st = $pdo->prepare("UPDATE pedidos SET notas=?, updated_at=? WHERE numero=?");
            $st->execute([$in['notas'] ?? '', $now, $numero]);
            if ($st->rowCount() === 0) arx_json(['error' => 'pedido no existe'], 404);
            arx_json(['ok' => true]);
        }
        default:
            arx_json(['error' => 'action desconocida: ' . $action], 400);
    }
} catch (Exception $e) {
    arx_json(['error' => $e->getMessage()], 500);
}
