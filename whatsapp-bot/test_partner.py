import sys
sys.path.insert(0, "/app")
from main import odoo_resolve_partner, _odoo_partner_brief
import sqlite3

# Resolver el phone que ya tenemos en DB
conn = sqlite3.connect("/opt/whatsapp-bot/data/conversations.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT phone, name, odoo_partner_id FROM conversations").fetchall()
for r in rows:
    print(f"phone={r['phone']} name={r['name']} partner_id={r['odoo_partner_id']}")
    if not r['odoo_partner_id']:
        result = odoo_resolve_partner(r['phone'], r['name'])
        print(f"  resolve() → {result}")
        if result:
            conn.execute("UPDATE conversations SET odoo_partner_id=? WHERE phone=?",
                       (result['id'], r['phone']))
            conn.commit()
    else:
        brief = _odoo_partner_brief(r['odoo_partner_id'])
        print(f"  brief() → {brief}")
