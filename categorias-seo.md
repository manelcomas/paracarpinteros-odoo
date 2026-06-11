# Propuesta SEO categorías — www.paracarpinteros.com

Fecha: 2026-06-11. **Solo propuesta — nada aplicado en Odoo.**

Fuente: lectura XML-RPC de las 158 `product.public.category`. Las 8 raíces
(Fresas y Router, Herrajes, Máquinas, Taladro y Brocas, etc.) y las subcategorías
de "Fresas y Router" **ya tienen** meta title/description del trabajo SEO anterior
— no se tocan. Lo que falta son las subcategorías de los árboles pedidos
(fresas G/P/V, brocas, herrajes, aceites, máquinas), que hoy están sin metas.

## Observación previa: árboles de fresas duplicados

Existen **dos árboles paralelos de fresas**:

- `Fresas y Router` (id 1884) con subcategorías ya optimizadas (mango 1/2" = 115
  productos, mango 1/4" = 99, CNC = 37…)
- `Fresas` (id 2035, sin metas) con `Fresas G - Vástago 1/2"` (133), `Fresas P -
  Vástago 1/4"` (137), `Fresas V - Especiales CNC` (40)

Ambos compiten por las mismas keywords ("fresas para router costa rica") →
**canibalización**. La propuesta de abajo renombra el árbol G/P/V a nombres de
carpintero manteniendo la jerarquía interna, pero a medio plazo conviene decidir
si se fusionan los dos árboles o se deja uno fuera del menú público.

## Propuesta (nombre público + meta title ≤60 + meta description ≤155)

La "ref interna" (G/P/V) se mantiene en la jerarquía, solo cambia el nombre
visible. Convención del title: keyword + "Costa Rica" + marca cuando cabe.

### Fresas (árbol interno G/P/V)

| id | Actual | Nombre público propuesto | Meta title | Meta description |
|---|---|---|---|---|
| 2035 | Fresas | Fresas para Router y CNC | Fresas para Router y CNC en Costa Rica \| ParaCarpinteros | Más de 300 fresas para router y CNC: rectas, copiadoras, grabado en V y espirales. Vástago 1/2", 1/4" y 6 mm. Stock real y envío a toda Costa Rica. |
| 2036 | Fresas G - Vástago 1/2" | Fresas para Router – Vástago 1/2" | Fresas para Router 1/2" en Costa Rica \| ParaCarpinteros | Fresas profesionales con vástago de 1/2" para router de mesa: rectas, copiadoras, molduras y paneles. De carpintero a carpintero, envío en Costa Rica. |
| 2037 | Fresas P - Vástago 1/4" | Fresas para Router – Vástago 1/4" | Fresas para Router 1/4" en Costa Rica \| ParaCarpinteros | Fresas con vástago de 1/4" para router manual y trimmer: rectas, redondeo, copiadoras y cola de milano. Stock real y envío a toda Costa Rica. |
| 2038 | Fresas V - Especiales CNC | Fresas para CNC | Fresas para CNC Costa Rica \| Up-Cut, Down-Cut y 3D | Fresas espirales para CNC en metal duro: up-cut, down-cut, compresión, punta bola para 3D y grabado en V. Envío a toda Costa Rica. |

### Taladro y Brocas (subcategorías)

| id | Actual | Nombre público propuesto | Meta title | Meta description |
|---|---|---|---|---|
| 2028 | Brocas | Brocas para Madera | Brocas para Madera Costa Rica \| ParaCarpinteros | Brocas para madera de todo tipo: helicoidales, de paleta, escalonadas y para bisagras. 80 modelos con stock real y envío a toda Costa Rica. |
| 2029 | Brocas Forstner | Brocas Forstner | Brocas Forstner Costa Rica \| ParaCarpinteros | Brocas Forstner para perforaciones limpias de fondo plano: medidas métricas y en pulgadas, ideales para bisagras de cazoleta. Envío a toda Costa Rica. |
| 2027 | Avellanadores | Avellanadores | Avellanadores para Madera Costa Rica \| ParaCarpinteros | Avellanadores y brocas avellanadoras para tornillos en madera: cabeza limpia sin astillar. Varios diámetros, stock real y envío a toda Costa Rica. |
| 2025 | Accesorios taladro | Accesorios para Taladro | Accesorios para Taladro Costa Rica \| ParaCarpinteros | Guías de perforación, topes de profundidad, adaptadores y extensiones para taladro. Perforá recto y a la medida. Envío a toda Costa Rica. |
| 2026 | Afiladores de brocas | Afiladores de Brocas | Afiladores de Brocas Costa Rica \| ParaCarpinteros | Afiladores para recuperar el filo de brocas de madera y metal sin equipos caros. Fáciles de usar, con envío a toda Costa Rica. |

### Herrajes y Tornillería (subcategorías principales)

| id | Actual | Nombre público propuesto | Meta title | Meta description |
|---|---|---|---|---|
| 1967 | Bisagras | Bisagras para Muebles | Bisagras para Muebles Costa Rica \| Cazoleta y más | Bisagras de cazoleta, piano, invisibles, de barril y decorativas para muebles y cajas. 45 modelos con stock y envío a toda Costa Rica. |
| 1986 | Tornillos | Tornillos para Madera | Tornillos para Madera Costa Rica \| ParaCarpinteros | Tornillos para madera y melamina: cabeza plana, torx, rosca gruesa y fina. Cajas por cantidad con envío a toda Costa Rica. |
| 1987 | Tuercas | Tuercas e Insertos para Madera | Insertos y Tuercas para Madera Costa Rica | Tuercas de embutir, insertos roscados y tuercas T para madera. Uniones firmes y desmontables. Stock real y envío a toda Costa Rica. |
| 1988 | Uniones y conectores para muebles | Uniones Minifix y Conectores | Minifix Costa Rica \| Uniones para Muebles | Uniones minifix, excéntricas y conectores para armado de muebles en melamina y madera. De carpintero a carpintero, envío a toda Costa Rica. |
| 1974 | Manijas y tiradores | Manijas y Tiradores | Manijas y Tiradores para Muebles Costa Rica | Manijas y tiradores para muebles, gavetas y puertas en estilos modernos y clásicos. Envío a toda Costa Rica. |
| 1980 | Rieles T-Track | Rieles T-Track | Rieles T-Track Costa Rica \| ParaCarpinteros | Rieles T-Track y accesorios para mesas de trabajo y jigs: topes, mariposas y pernos. Armá tu banco a la medida. Envío a toda Costa Rica. |
| 1985 | Tarugos | Tarugos y Espigas de Madera | Tarugos de Madera Costa Rica \| ParaCarpinteros | Tarugos y espigas de madera en varias medidas para uniones fuertes e invisibles. Stock real y envío a toda Costa Rica. |
| 1969 | Cierres decorativos | Cierres para Cajas | Cierres para Cajas de Madera Costa Rica | Cierres decorativos, broches y pasadores para cajas de madera, joyeros y cofres. Envío a toda Costa Rica. |
| 1968 | Cerraduras y pestillos | Cerraduras y Pestillos | Cerraduras para Muebles Costa Rica \| Pestillos | Cerraduras para muebles, gavetas y vitrinas, pestillos y portacandados. Stock real y envío a toda Costa Rica. |
| 1984 | Soportes para estantes | Soportes para Estantes | Soportes para Estantes Costa Rica \| Flotantes | Soportes y ménsulas para estantes flotantes y repisas: ocultos y decorativos. Envío a toda Costa Rica. |

### Aceites y acabados

| id | Actual | Nombre público propuesto | Meta title | Meta description |
|---|---|---|---|---|
| 1891 | Aceites y acabados | Aceites para Madera | Aceite de Tung y Aceites para Madera Costa Rica | Aceite de tung puro, aceite de linaza y acabados naturales que protegen y realzan el veteado de la madera. Envío a toda Costa Rica. |
| 1894 | Ceras | Ceras para Madera | Cera para Madera Costa Rica \| ParaCarpinteros | Ceras para acabado y protección de la madera: brillo natural y tacto suave. El complemento del aceite de tung. Envío a toda Costa Rica. |

### Máquinas (subcategorías principales)

| id | Actual | Nombre público propuesto | Meta title | Meta description |
|---|---|---|---|---|
| 2015 | CNC | Router CNC | Router CNC Costa Rica \| Máquinas para Carpintería | Máquinas router CNC para carpintería y grabado: kits, husillos y repuestos. Asesoría técnica real y envío a toda Costa Rica. |
| 2016 | CNC Láser | Grabadoras Láser | Grabadora Láser Costa Rica \| ParaCarpinteros | Grabadoras láser para madera, MDF y acrílico: máquinas, módulos, lentes y repuestos. Asesoría de carpintero a carpintero. Envío en Costa Rica. |
| 2013 | Amoladoras / Pulidoras | Amoladoras y Pulidoras | Amoladoras y Pulidoras Costa Rica \| Tallado | Amoladoras y pulidoras para tallado, desbaste y acabado en madera, con discos y escofinas compatibles. Envío a toda Costa Rica. |
| 2017 | Cepilladoras / Canteadoras | Cepilladoras y Canteadoras | Cepilladoras y Canteadoras Costa Rica | Cepilladoras y canteadoras para madera, más cuchillas y repuestos. Tablas listas para ensamblar. Envío a toda Costa Rica. |
| 2021 | Neumática | Herramienta Neumática | Herramienta Neumática Costa Rica \| Clavadoras | Clavadoras, engrapadoras y accesorios neumáticos para el taller de carpintería. Envío a toda Costa Rica. |
| 2022 | Routers | Routers y Trimmers | Router para Carpintería Costa Rica \| Trimmers | Routers y trimmers para carpintería, con fresas, placas base y accesorios compatibles. Envío a toda Costa Rica. |
| 2018 | Enchapadoras | Enchapadoras de Cantos | Enchapadoras de Cantos Costa Rica | Enchapadoras de cantos manuales y accesorios para enchapado de melamina y madera. Envío a toda Costa Rica. |

## Sin cambios (ya optimizadas en el trabajo SEO anterior)

- 1884 Fresas y Router (raíz) y sus 15 subcategorías (mango 1/2", 1/4", CNC, copiadoras…)
- 1885 Herrajes y Tornillería, 1888 Máquinas, 1889 Taladro y Brocas, 1881 Abrasivos
  y Acabados (raíces con meta title/description correctos)

## Cómo se aplicaría (cuando des el OK)

Script `scripts/aplicar_categorias_seo.py` (a crear tras tu OK, dry-run por
defecto): `write` por XML-RPC sobre `product.public.category` de `name`,
`website_meta_title` y `website_meta_description` solo de los ids listados.
No toca productos, precios ni jerarquía (`parent_id` queda igual).
