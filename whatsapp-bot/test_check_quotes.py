import sys, datetime as dt
sys.path.insert(0, "/app")
import xmlrpc.client
from main import ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, odoo_authenticate

uid = odoo_authenticate()
models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)

# Cotizaciones de Manuel Comas (32910) creadas hoy
today = dt.date.today().strftime("%Y-%m-%d 00:00:00")
ids = models.execute_kw(
    ODOO_DB, uid, ODOO_API_KEY,
    "sale.order", "search",
    [[("partner_id", "=", 32910), ("create_date", ">=", today)]],
    {"order": "id desc"}
)
print(f"Cotizaciones de Manuel Comas hoy: {len(ids)}")
if ids:
    rows = models.execute_kw(
        ODOO_DB, uid, ODOO_API_KEY,
        "sale.order", "read",
        [ids],
        {"fields": ["name", "state", "amount_total", "create_date", "order_line"]}
    )
    for r in rows:
        print(f"  {r['name']} · {r['state']} · ₡{r['amount_total']} · {r['create_date']} · {len(r['order_line'])} líneas")
else:
    print("  (ninguna cotización creada hoy por el bot)")
