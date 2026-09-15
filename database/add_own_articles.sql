-- Own articles and product names belonging to Svetoyar.
--
-- The file светнн1.xlsx was first read as an eighth supplier, "Свет НН". It is
-- not one. The client confirmed on 2026-09-15 that it is the Svetoyar price
-- list, and the file says so itself: its «Производитель» column holds only
-- «Россия» and «Светояр», the string "Свет НН" appears nowhere in it, every row
-- carries a Salux order marking, and its dealer price equals the Salux
-- distributor tier exactly.
--
-- What the file really provides is the Svetoyar article and name for
-- goods Salux manufactures - «Наше наименование» in the source. That belongs
-- beside the catalogue, not inside supplier_products, because it is our data
-- rather than a supplier feed, and it must survive every price list reload.
--
-- Matching back to the supplier row is by order marking.

USE svetoyar_staging;

CREATE TABLE IF NOT EXISTS own_articles (
  id                INT UNSIGNED    NOT NULL AUTO_INCREMENT,
  own_sku           VARCHAR(64)     NOT NULL COMMENT 'артикул Светояра',
  own_name          VARCHAR(512)    NULL COMMENT 'наше наименование',
  supplier_id       INT UNSIGNED    NULL COMMENT 'whose goods these are',
  supplier_marking  VARCHAR(255)    NULL COMMENT 'links to supplier_products.supplier_sku',
  supplier_name     VARCHAR(512)    NULL COMMENT 'the name the supplier uses, for reference',
  dealer_price      DECIMAL(12,4)   NULL COMMENT 'what we pay - equals the Salux distributor tier',
  category          VARCHAR(255)    NULL,
  attributes_json   JSON            NULL,
  source_file       VARCHAR(255)    NULL,
  created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_own_articles (own_sku),
  KEY idx_own_articles_marking (supplier_marking),
  CONSTRAINT fk_own_articles_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Remove the phantom supplier. Its supplier_products, supplier_sources and
-- pricing rules all cascade from this row. Nothing downstream is lost: neither
-- Salux nor "Свет НН" had reached products, links, offers or the moderation
-- queue when this ran.
DELETE FROM suppliers WHERE code = 'svetnn';
