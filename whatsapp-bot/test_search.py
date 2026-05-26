import os, sys
sys.path.insert(0, "/app")
from main import search_products_odoo, _tokenize_query

queries = [
    "avellanador 8mm",
    "avellanador 8 mm",
    "tenés avellanador de 8 mm",
    "broca avellanadora 8mm",
    "bisagra decorativa",
    "sierra circular 7 1/4",
    "tornillo phillips",
]
print("=== TOKENIZACIÓN ===")
for q in queries:
    print(f"  {q!r}  →  {_tokenize_query(q)}")

print("\n=== BÚSQUEDA REAL ===")
for q in queries:
    rows = search_products_odoo(q, limit=3)
    print(f"\n--- {q!r}  ({len(rows)} resultados) ---")
    for r in rows:
        code = r['codigo'] or '—'
        print(f"  [{code}] {r['nombre'][:70]}  ₡{r['precio_crc']:,}  stock={r['stock']}")
    if not rows:
        print("  (sin resultados)")
