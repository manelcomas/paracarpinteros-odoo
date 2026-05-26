import sqlite3
c = sqlite3.connect('/opt/whatsapp-bot/data/conversations.db')
c.row_factory = sqlite3.Row
cols = [r[1] for r in c.execute('PRAGMA table_info(conversations)').fetchall()]
print('Columnas:', cols)
print('\nConversaciones:')
for r in c.execute('SELECT phone, name, status, odoo_partner_id, odoo_sale_order_name FROM conversations').fetchall():
    print(' ', dict(r))
