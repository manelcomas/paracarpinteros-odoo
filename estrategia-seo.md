# Estrategia SEO — paracarpinteros.com

Fecha: 2026-06-11. Basada en los datos reales de Search Console (90 días:
2.917 clicks, 188.730 impresiones, 353 oportunidades en posición 5–20) y en la
infraestructura ya desplegada hoy (schema Product completo, feed Merchant,
28 categorías optimizadas, GSC por API).

**Lectura global:** el sitio tiene autoridad de marca ("la juguetería…" = 18% de
los clicks) y rankea bien en nichos de producto (aceites, herrajes de puerta
corrediza, helicoil), pero el 75% de las impresiones está atrapado en
posiciones 7–11 de páginas de herramientas, y hay fugas técnicas (404 que
siguen rankeando). La estrategia: tapar fugas → exprimir lo que ya casi rankea
→ construir el embudo producto.

---

## P1 — Tapar la fuga: 404 que siguen rankeando ✅ EJECUTADO 2026-06-11

El inventario completo encontró **74 URLs muertas** (no 4): todas categorías del
árbol viejo, sumando **31.778 impresiones y 672 clicks** en 90 días. Se crearon
74 redirecciones 301 vía `website.rewrite` con `scripts/aplicar_redirects_seo.py`
(mapa slug→categoría viva, destinos canónicos sin cadenas). Verificado en vivo,
querystrings se conservan. La tabla original de la propuesta (parcial):

Google aún rankea **categorías del árbol viejo que hoy dan 404**:

| URL muerta | Query que rankea | Imp. | Pos. | Redirigir a (categoría nueva) |
|---|---|---|---|---|
| /shop/category/fresas-y-accesorios-para-router-172 | fresas para router (+ costa rica) | 463+58 | 16.3/5.2 | Fresas y Router (1884) |
| /shop/category/herrajes-y-tornilleria-190 | herrajes, herrajes para muebles cr | 171+66 | 7.5/6.5 | Herrajes y Tornillería (1885) |
| /shop/category/maquinas-185 | maquina cnc | 62 | 8.9 | Máquinas (1888) |
| /shop/category/para-router-157 | router para madera costa rica | 60 | 5.9 | Fresas y Router (1884) o Routers (2022) |

**Acción:** redirecciones 301 vía `website.rewrite` en Odoo (se puede crear por
XML-RPC; script `aplicar_redirects_seo.py` con dry-run). Además, inventariar el
resto: consultar GSC por dimensión `page`, probar status de cada URL y redirigir
todo lo que dé 404 con impresiones. Nota: `maquinas-185` aparece con 60 clicks
en el top de páginas — eso es tráfico que HOY cae en un 404.

## P2 — La mina: conversores mm↔pulgadas (esfuerzo: medio · impacto: el mayor del sitio)

**88.614 impresiones** (75% de todas las oportunidades) en ~200 variantes de la
misma intención, casi todas hacia `/conversion-de-medidas-tabla-de-pulgadas-milimetros-y-fracciones`,
en posición 7–11 con CTR < 0,5%. Pasar de pos ~10 a ~4 multiplicaría los clicks
del sitio entero.

Acciones, en orden:
1. **Title/meta description** de la página orientados a la query dominante:
   "Convertidor de mm a pulgadas (y pulgadas a mm) con tabla de fracciones" —
   hoy el title es descriptivo pero no replica el lenguaje de búsqueda.
2. **Resolver la competencia interna**: `/tabla-conversiones-pulgadas-a-mm` rankea
   para las variantes con fracción ("7/32 a mm", "0.375 pulgadas a mm"…) y la
   página grande para el resto. Decidir: o se especializan los titles (una =
   "mm→pulgadas", otra = "fracciones/decimales→mm") o se consolida con 301.
   No dejarlas compitiendo por lo mismo.
3. **H1 + primer párrafo** con la palabra "convertidor" (la página es la tabla;
   si ya hay conversor interactivo, ponerlo arriba del fold).
4. Realismo: es tráfico informacional (carpinteros y no carpinteros), no compra
   directa — su valor es marca, enlaces y remarketing. Por eso va después de P1
   pero no se le dedica contenido infinito: title + estructura y a otra cosa.

## P3 — Cluster "aceites y acabados": el nicho transaccional ganador (esfuerzo: medio · impacto: ventas)

Es la categoría reina en búsqueda no-marca y TODA la intención es de compra:
linaza (2.285 imp pos 11 + 963 "donde comprar" pos 8), tung (920+889 pos 7–8),
cera de abeja (top 5 ya), más el galón (404 imp pos 6).

1. **Hub**: la categoría "Aceites para Madera" (1891, optimizada hoy) debe
   enlazar y ser enlazada: ficha linaza 1086 ↔ ficha tung 6444 ↔ galón 2711 ↔
   post del blog de linaza (1.341 imp pos 6.4) ↔ categoría. Hoy el blog no
   enlaza a las fichas (revisar) y las fichas no se enlazan entre sí.
2. **Title ficha 1086**: añadir "Costa Rica" — "aceite de linaza para madera"
   (2.285 imp) está en pos 11.3 y la variante con "costa rica" en pos 2.8: el
   gap es señal de que falta relevancia local en la ficha.
3. **Contenido**: un post "Aceite de tung vs aceite de linaza: cuál usar"
   enlazando ambas fichas — captura las dos familias de queries a la vez.
4. Réplica del patrón en el siguiente nicho que ya asoma: **perfiles de
   aluminio** (526+416+159 imp pos 6–8, fichas 2020/4040 + categorías nuevas).

## P4 — Resolver la canibalización de fresas (esfuerzo: decisión + 1h · impacto: medio)

"fresas para router" (463 imp) rankea con una **URL muerta** (la 172) en pos 16,
y "fresas para router costa rica" (58 imp) igual — mientras hay DOS árboles
vivos compitiendo ("Fresas y Router" 1884 y el G/P/V renombrado hoy). Google no
sabe cuál es la canónica.

**Decisión pendiente tuya**: fusionar (mover los productos del G/P/V al árbol
1884 y redirigir) o dejar G/P/V fuera del menú/sitemap. Mi recomendación:
fusionar — un solo hub "Fresas para Router y CNC" que reciba el 301 de la 172 y
de las G/P/V. Junto con P1, le da a Google una sola puerta para toda la
demanda de fresas.

## P5 — Activar Merchant Center (esfuerzo: tuyo, 30 min · impacto: nuevo canal)

Todo lo técnico está listo (feed con 1.347 items regenerado a diario, schema con
sku/precio/stock, dominio verificado). Falta solo: crear la cuenta en
https://merchants.google.com con la cuenta de Google del negocio, reclamar el
sitio (la verificación del dominio ya está), añadir el feed por URL
(`https://panel.paracarpinteros.com/feed-google.xml`, frecuencia diaria) y
activar "plataformas gratuitas" (free listings). Con eso las fichas salen en la
pestaña Shopping sin pagar.

## P6 — Blog: mejorar CTR de lo que ya rankea antes de escribir nada nuevo

El blog ya está en pos 5–8 para queries de volumen: cedro amargo (937), teca
(502+382+186), laurel (100), atomstack x20 pro (410), "como se cura la madera"
(250). Acciones baratas: titles de esos 5–6 posts con el patrón
"keyword + Costa Rica + gancho" y un bloque de productos relacionados al final
de cada uno (la teca/cedro → aceites y herramientas de acabado; atomstack →
categoría Grabadoras Láser nueva). Posts nuevos solo donde el xlsx muestre
demanda sin página (revisar pestaña Oportunidades, columna página).

## Medición y cadencia

- `python3 scripts/gsc_keywords.py` **mensual** (1 min) y comparar resumen.md
  contra el del mes anterior (guardar como `resumen-YYYY-MM.md`).
- KPI a 90 días: clicks no-marca ×2 (de ~1.500 a 3.000), las queries de P2 de
  pos ~10 a ≤5, cero 404 con impresiones.
- En Search Console (UI): enviar el sitemap si aún no está, y vigilar
  Cobertura → "No encontrada (404)".

## Reparto

| Quién | Qué |
|---|---|
| Claude (con tu OK por escrito, dry-run primero) | P1 redirects 301 · P2 titles/metas de las 2 páginas de conversión · P3 interlinking + title ficha 1086 · P4 ejecución de la fusión cuando decidás · P6 titles de posts |
| Manel | P5 Merchant Center (cuenta) · decisión P4 (fusionar vs ocultar) · OK a cada lote de cambios |

Orden sugerido de ejecución: **P1 hoy** (es una fuga activa), P2+P3 esta semana,
P4 cuando decidás, P5 cuando tengás 30 min, P6 la semana que viene.
