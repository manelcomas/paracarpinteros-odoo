FROM php:8.2-apache

# Dependencias del sistema
RUN apt-get update && apt-get install -y \
    git \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Habilitar mod_rewrite y CORS
RUN a2enmod rewrite headers

# OpenSSL legacy provider para .p12 antiguos de Hacienda CR (RC2-40-CBC)
RUN { \
      echo "openssl_conf = openssl_init"; \
      echo ""; \
      echo "[openssl_init]"; \
      echo "providers = provider_sect"; \
      echo ""; \
      echo "[provider_sect]"; \
      echo "default = default_sect"; \
      echo "legacy = legacy_sect"; \
      echo ""; \
      echo "[default_sect]"; \
      echo "activate = 1"; \
      echo ""; \
      echo "[legacy_sect]"; \
      echo "activate = 1"; \
    } > /etc/ssl/openssl-legacy.cnf
ENV OPENSSL_CONF=/etc/ssl/openssl-legacy.cnf

# Clonar CRLibre (lib de firma probada en producción CR)
RUN git clone --depth 1 https://github.com/CRLibre/API_Hacienda.git /opt/crlibre

# Patch: Hacienda PROD rechazaba con "La firma del documento no tiene el Policy Id".
# CRLibre pone el Identifier URL-encoded ("Resoluci%C3%B3n_..."). Probamos con la
# URL decodificada (UTF-8 literal) por si el validator de Hacienda compara por
# string-equality contra la URL decoded.
RUN sed -i 's|Resoluci%C3%B3n_General_sobre_disposiciones_t%C3%A9cnicas_comprobantes_electr%C3%B3nicos_para_efectos_tributarios|Resolución_General_sobre_disposiciones_técnicas_comprobantes_electrónicos_para_efectos_tributarios|' /opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php \
    && grep -c "Resolución_General_sobre_disposiciones_técnicas" /opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php

# Patch 2: Reference[0] declara solo "enveloped-signature" pero CRLibre digiere
# el XML c14n-izado. Hacienda al validar hace AS-IS digest (no canoniza por su
# cuenta) y rechaza "El XML fue modificado luego de haber sido firmado".
# Fix: añadir explícitamente el Transform C14N después del enveloped-signature.
RUN sed -i "s|'<ds:Transform Algorithm=\"http://www.w3.org/2000/09/xmldsig#enveloped-signature\" />' \.|'<ds:Transform Algorithm=\"http://www.w3.org/2000/09/xmldsig#enveloped-signature\" />' . '<ds:Transform Algorithm=\"http://www.w3.org/TR/2001/REC-xml-c14n-20010315\" />' .|" /opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php \
    && grep -c "REC-xml-c14n-20010315.*REC-xml-c14n-20010315" /opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php || \
       grep -c "Transform Algorithm.*REC-xml-c14n-20010315.*/>" /opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php

# Patch 3: CRLibre inyecta namespaces inline antes de canonicalizar KeyInfo y
# SignedProperties para calcular digests. Esos namespaces NO coinciden con los
# del root del XML real: CRLibre añade xmlns:xsd (que no está en el root) y
# para KeyInfo NO añade xmlns:xades (que sí está en el root). Como Hacienda
# valida los digests in-context (con los namespaces visibles del documento),
# canoniza diferente y obtiene digest distinto → rechaza "modificado".
# Fix: matchear los namespaces inline con los del root (sin xmlns:xsd, con
# xmlns:xades en KeyInfo).
RUN sed -i "0,/xmlns:xsd=.*XMLSchema/{s|xmlns:xsd=\"http://www.w3.org/2001/XMLSchema\"|xmlns:xades=\"http://uri.etsi.org/01903/v1.3.2#\"|}" /opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php \
    && sed -i "/xmlns:xsd=.*XMLSchema/d" /opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php \
    && echo "=== verificar (no debe haber xmlns:xsd) ===" \
    && (grep -n "xmlns:xsd" /opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php && echo "FALLO: queda xmlns:xsd" && exit 1 || echo "OK: ningun xmlns:xsd restante")

# Patch 4: $xmnls_signeg (namespaces para canonicalizar el SignedInfo antes
# de firmar con openssl_sign) NO incluye xmlns:xades. Pero el verificador
# canonicaliza el SignedInfo in-context y SÍ ve xmlns:xades (heredado del
# root). Bytes canonicalizados distintos → digest del SignedInfo distinto →
# openssl_verify del lado de Hacienda falla → "SIG_CRYPTO_FAILURE".
# Fix: añadir xmlns:xades al $xmnls_signeg.
RUN sed -i "/\\\$xmnls_signeg .= 'xmlns:ds/a\\                        'xmlns:xades=\"http://uri.etsi.org/01903/v1.3.2#\" ' ." /opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php \
    && echo "=== verificar (xmlns:xades count debe ser 5 ahora) ===" \
    && grep -c "xmlns:xades" /opt/crlibre/api/contrib/signXML/Firmadohaciendacr.php

# Copiar nuestro endpoint
COPY sign.php /var/www/html/sign.php
COPY health.php /var/www/html/health.php

# v31: Buzón Recepción — backend OAuth Gmail (PHP nativo, sin composer)
COPY buzon-rx/ /var/www/html/buzon-rx/
# Carpeta de storage persistente (montada como volumen en docker-compose)
RUN mkdir -p /var/www/html/buzon-rx/storage \
    && chown -R www-data:www-data /var/www/html/buzon-rx \
    && chmod -R 775 /var/www/html/buzon-rx/storage

# Apache config: rewrite de /sign, /health y /buzon-rx/*
COPY apache-fe.conf /etc/apache2/conf-enabled/fe.conf

# Quitar index.html default
RUN rm -f /var/www/html/index.html

# El entrypoint estándar de php:apache ya levanta apache en :80
