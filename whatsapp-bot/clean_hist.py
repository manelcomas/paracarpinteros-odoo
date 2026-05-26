import sqlite3
conn = sqlite3.connect("/opt/whatsapp-bot/data/conversations.db")
conn.row_factory = sqlite3.Row

phone = "50686069717"
print("=== ANTES (últimos 12) ===")
for r in conn.execute(
    "SELECT id, direction, substr(text,1,80) as t, bot_replied FROM messages WHERE phone=? ORDER BY ts DESC LIMIT 12",
    (phone,)
).fetchall():
    flag = "bot" if r["bot_replied"] else ""
    print(f"  #{r['id']:>4} [{r['direction']:3}] {flag:3} {r['t']}")

# Borrar mensajes outbound del bot que mencionan "S07162" (alucinaciones)
cur = conn.execute(
    "DELETE FROM messages WHERE phone=? AND direction='out' AND bot_replied=1 AND text LIKE '%S07162%'",
    (phone,)
)
print(f"\n>>> Borrados {cur.rowcount} mensajes alucinados con 'S07162'")

# También borrar cualquier mensaje out del bot que diga "te armé la cotización" sin "S0" después
# (por ahora simplemente los del historial reciente del usuario alucinado)
cur2 = conn.execute(
    "DELETE FROM messages WHERE phone=? AND direction='out' AND bot_replied=1 AND (text LIKE '%armé la cotización%' OR text LIKE '%arme la cotizacion%')",
    (phone,)
)
print(f">>> Borrados {cur2.rowcount} mensajes 'armé la cotización' sin orden real")

conn.commit()

print("\n=== DESPUÉS (últimos 12) ===")
for r in conn.execute(
    "SELECT id, direction, substr(text,1,80) as t, bot_replied FROM messages WHERE phone=? ORDER BY ts DESC LIMIT 12",
    (phone,)
).fetchall():
    flag = "bot" if r["bot_replied"] else ""
    print(f"  #{r['id']:>4} [{r['direction']:3}] {flag:3} {r['t']}")
