# Auditoría schema.org Product — www.paracarpinteros.com

- Fecha: 2026-06-11 14:21
- Fichas muestreadas: 15 de 1358 en el sitemap (seed 2026)
- Campos verificados: name, image, sku, offers.price, offers.priceCurrency=CRC, offers.availability
- Fichas con algún campo faltante: 15/15

| URL | Campos faltantes | Nota |
|---|---|---|
| https://www.paracarpinteros.com/shop/cnc-prt-de-router-4040-motor-775-alta-precision-y-eficiencia-para-proyectos-de-grabado-400mm-x-400-mm-6349 | sku |  |
| https://www.paracarpinteros.com/shop/fresa-de-corte-recto-10-mm-38-vastago-de-14-refp62-6257 | sku |  |
| https://www.paracarpinteros.com/shop/a2184-piedra-de-afilar-perfilada-para-gubias-y-formones-grano-1000-azul-grano-3000-amarilla-grano-280-naranja-6849 | sku |  |
| https://www.paracarpinteros.com/shop/pinza-portaherramientas-er20-de-317-mm-abrazadera-de-resorte-para-maquina-de-grabado-cnc-1358 | sku |  |
| https://www.paracarpinteros.com/shop/tuerca-de-embutir-14-x-15mm-insertos-para-madera-14-635mm-25-unidades-6401 | sku |  |
| https://www.paracarpinteros.com/shop/broca-forstner-8-mm-tipo-70-izquierdo-6828 | sku |  |
| https://www.paracarpinteros.com/shop/electrodo-de-grafito-con-recubrimiento-de-cobre-para-soldar-a-bajo-voltaje-6355-mm-1200 | sku |  |
| https://www.paracarpinteros.com/shop/soporte-para-router-ranurador-2-en-1-para-carpinteria-fijadores-invisibles-6390 | sku |  |
| https://www.paracarpinteros.com/shop/tope-para-mesa-de-carpintero-tipo-perro-19mm-2105 | sku |  |
| https://www.paracarpinteros.com/shop/a2089-rodamiento-frontal-para-tapeteadora-6719 | sku |  |
| https://www.paracarpinteros.com/shop/a2093-hs200-200kgs-bisagra-invisible-6726 | sku |  |
| https://www.paracarpinteros.com/shop/suizan-cepillo-japones-kanna-handplane-150-mm-1389 | sku |  |
| https://www.paracarpinteros.com/shop/regla-articulada-localizadora-de-agujeros-1534 | sku |  |
| https://www.paracarpinteros.com/shop/perfil-de-aluminio-4040-100-cm-tipo-t-6903 | sku |  |
| https://www.paracarpinteros.com/shop/sierra-de-16-para-corte-de-bambu-2196 | sku |  |

## Hallazgos

1. **El JSON-LD Product lo genera Odoo 19 server-side (Python, no QWeb).** No existe
   ninguna `ir.ui.view` con `ld+json` de producto (verificado por XML-RPC: solo
   `website_sale_layout` y forum tienen ld+json, y ninguna vista contiene
   `BreadcrumbList`). Por tanto **no se puede tocar por vista heredada**.
2. **Lo que SÍ emite Odoo** en cada ficha: `name`, `url`, `image` (image_1920),
   `offers.price`, `offers.priceCurrency: CRC`, `offers.availability`
   (InStock/OutOfStock según stock real) y `description` (usa la
   website_meta_description que ya pusimos). También emite `BreadcrumbList` con la
   categoría. No hay microdata (no hace falta, JSON-LD basta para Google).
3. **Lo ÚNICO que falta es `sku`** — en el 100% de la muestra. Verificado que Odoo
   no lo emite ni siquiera cuando el producto tiene `default_code` (probado con
   A465). 1350 de 1359 productos publicados tienen `default_code`.
4. `image` NO falta (la tarea preguntaba por ese caso) — no hace falta inyectarla.

## Propuesta para inyectar `sku` (pendiente de OK)

El `default_code` ya está impreso en el DOM de cada ficha por Odoo nativo:
`<p class="text-muted">Ref: A465</p>`. La vía realista en Odoo Online:

**Opción A (recomendada): snippet JS en el custom code del website** (el mismo
`website.custom_code_head`/`footer` del website 3 que ya usamos para la búsqueda y
la burbuja WA). ~15 líneas: en páginas `/shop/*`, leer `Ref: XXX` del DOM (o el
`data-product-tracking-info` que también lleva el id), parsear el `<script
type="application/ld+json">` existente, añadir `sku` al objeto Product y reescribir
el script. Google ejecuta JS al renderizar e indexa structured data generado por
JS (documentado por Google), así que cuenta para rich results.
- Pros: un solo cambio, cubre los 1350 productos con ref, cero riesgo de datos.
- Contras: depende del render JS de Googlebot (días de retraso vs. crawl HTML).

**Opción B (complementaria, gratis): no depender del schema para el sku.** Para
Merchant Center el `g:id` del feed (Tarea 2) es la clave del producto; el schema
de la página solo necesita que `price`/`availability` coincidan con el feed (y
coinciden, ambos salen de Odoo). El `sku` ausente genera como mucho un warning de
"structured data sin sku" en Merchant, no una desaprobación.

**Recomendación:** hacer la Opción A (es barata y elimina el warning) y no
perseguir nada más: el schema nativo de Odoo ya es elegible para rich results de
producto (precio + disponibilidad + imagen es lo que Google exige como mínimo).
