import sqlite3
c = sqlite3.connect("/opt/whatsapp-bot/data/conversations.db")
c.row_factory = sqlite3.Row
total = c.execute("SELECT COUNT(*) FROM bot_knowledge").fetchone()[0]
print(f"Total entradas knowledge: {total}\n")
for r in c.execute("SELECT id, category, title, substr(content,1,80) as preview, active FROM bot_knowledge ORDER BY sort_order").fetchall():
    flag = "✓" if r["active"] else "✕"
    print(f"  {flag} #{r['id']} [{r['category']:10s}] {r['title']}")
    print(f"     {r['preview']}...\n")
