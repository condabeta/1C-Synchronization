-- Supplier values that a protected field turned away.
--
-- Descriptions, names and specs are protected from supplier imports, so a
-- manager's edits survive a price list reload. Until now the value the supplier
-- sent was simply dropped. The client asked that differences go to moderation
-- instead, with bulk accept and reject, so nothing new a supplier sends is lost.
--
-- One pending row per product, supplier and field. A newer supplier value
-- replaces the pending one. A rejected value is remembered by its hash, so the
-- same text arriving in the next price list does not ask the manager again.

USE svetoyar_staging;

CREATE TABLE IF NOT EXISTS field_discrepancies (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  product_id      BIGINT UNSIGNED NOT NULL,
  supplier_id     INT UNSIGNED    NOT NULL,
  field_name      VARCHAR(64)     NOT NULL,
  current_value   MEDIUMTEXT      NULL COMMENT 'our value when the difference was last seen',
  supplier_value  MEDIUMTEXT      NULL COMMENT 'what the supplier sent',
  value_hash      CHAR(64)        NOT NULL COMMENT 'sha256 of the normalised supplier value',
  status          ENUM('pending', 'accepted', 'rejected') NOT NULL DEFAULT 'pending',
  detected_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  resolved_at     DATETIME        NULL,
  resolved_by     VARCHAR(128)    NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_field_discrepancies (product_id, supplier_id, field_name, value_hash),
  KEY idx_field_discrepancies_status (status, supplier_id, field_name),
  CONSTRAINT fk_field_discrepancies_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_field_discrepancies_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
