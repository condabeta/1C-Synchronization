-- Conformity documents and which products they cover.
--
-- The client needs registry links on product pages - a declaration or
-- certificate that can be checked against the official register. Three
-- suppliers publish registries: Arlight, LED Crystal and Salux. Dekomo, Jazzway,
-- SWG and ViaSvet do not, so their products get no documents from this.
--
-- The registries bind documents to products in two different ways. Arlight
-- lists every document against explicit article numbers. LED Crystal and Salux
-- publish documents by series, in prose, so their products are matched through
-- curated rules in staging/certificates.py instead.
--
-- A link is stored by supplier and article rather than by supplier_products.id,
-- because supplier rows are rewritten on every import and the article is what
-- stays stable.

USE svetoyar_staging;

CREATE TABLE IF NOT EXISTS certificates (
  id                  INT UNSIGNED    NOT NULL AUTO_INCREMENT,
  supplier_id         INT UNSIGNED    NOT NULL,
  code                VARCHAR(32)     NOT NULL COMMENT 'registry id - ARL-0007, CRY-04, SAL-05',
  doc_kind            ENUM('declaration', 'certificate', 'voluntary', 'refusal_letter',
                           'approval', 'quality_system', 'other')
                                      NOT NULL DEFAULT 'other',
  doc_kind_label      VARCHAR(160)    NULL COMMENT 'the document type as the registry spells it',
  doc_number          VARCHAR(160)    NULL,
  scope_text          TEXT            NULL COMMENT 'products or series the document names',
  regulation          VARCHAR(255)    NULL,
  valid_from          DATE            NULL,
  valid_to            DATE            NULL COMMENT 'NULL - no end date set, or not stated',
  registry_url        VARCHAR(1024)   NULL COMMENT 'official register only: FSA, Kyrgyz register, RKO',
  other_url           VARCHAR(1024)   NULL COMMENT 'a link that is not the official register - not for display as one',
  scan_url            VARCHAR(1024)   NULL,
  link_status         ENUM('official', 'unofficial', 'not_required', 'unconfirmed', 'pending')
                                      NOT NULL DEFAULT 'unconfirmed',
  is_product_document TINYINT(1)      NOT NULL DEFAULT 1
                      COMMENT '0 for documents that certify no product - ISO 9001, advertising letters',
  source              VARCHAR(255)    NULL,
  notes               TEXT            NULL,
  created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_certificates_code (supplier_id, code),
  KEY idx_certificates_valid (valid_to),
  CONSTRAINT fk_certificates_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS product_certificates (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  certificate_id  INT UNSIGNED    NOT NULL,
  supplier_id     INT UNSIGNED    NOT NULL,
  supplier_sku    VARCHAR(128)    NOT NULL,
  match_type      ENUM('explicit', 'series') NOT NULL
                  COMMENT 'explicit - named by the registry; series - matched by a curated rule',
  match_basis     VARCHAR(255)    NULL COMMENT 'the rule that matched, so a series link can be audited',
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_product_certificates (certificate_id, supplier_id, supplier_sku),
  KEY idx_product_certificates_product (supplier_id, supplier_sku),
  CONSTRAINT fk_product_certificates_certificate
    FOREIGN KEY (certificate_id) REFERENCES certificates(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_product_certificates_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
