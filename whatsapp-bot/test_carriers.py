import sys
sys.path.insert(0, "/app")
import xmlrpc.client
from main import ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, odoo_authenticate

uid = odoo_authenticate()
models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)

# Ver delivery.carrier configurados
print("=== delivery.carrier disponibles ===")
ids = models.execute_kw(
    ODOO_DB, uid, ODOO_API_KEY,
    "delivery.carrier", "search",
    [[]],
    {"order": "sequence asc, name asc"}
)
print(f"Total: {len(ids)} carriers")
rows = models.execute_kw(
    ODOO_DB, uid, ODOO_API_KEY,
    "delivery.carrier", "read",
    [ids],
    {"fields": ["name", "delivery_type", "active", "free_over", "fixed_price", "country_ids"]}
)
for r in rows:
    flag = "✓" if r["active"] else "✕"
    print(f"  {flag} #{r['id']:>3} | {r['name']:40s} | tipo={r['delivery_type']:15s} | precio_fijo={r.get('fixed_price')}")

# Verificar el sale.order S07166 — qué carrier tiene?
print("\n=== sale.order S07166 ===")
o = models.execute_kw(
    ODOO_DB, uid, ODOO_API_KEY,
    "sale.order", "search_read",
    [[("name", "=", "S07166")]],
    {"fields": ["name", "state", "carrier_id", "partner_id", "amount_total", "order_line"], "limit": 1}
)
print(o)
