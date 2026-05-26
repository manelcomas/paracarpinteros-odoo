import sys
sys.path.insert(0, "/app")
from main import search_products_odoo, _tokenize_query

queries = [
    "destornillador",
    "destornillador phillips",
    "destornillador mango naranja punta intercambiable",
    "destornillador punta intercambiable",
    "puntas",
    "bits",
    "porta puntas",
    "phillips",
    "punta destornillador",
    "kit destornillador",
    "atornillador",
]

for q in queries:
    tokens = _tokenize_query(q)
    rows = search_products_odoo(q, limit=3)
    print(f"\nQUERY {q!r} → tokens={tokens} → {len(rows)} resultados")
    for r in rows:
        print(f"  [{r['codigo']}] {r['nombre'][:60]}  ₡{r['precio_crc']:,}")
