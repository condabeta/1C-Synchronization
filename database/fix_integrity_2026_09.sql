-- =============================================================================
-- Integrity fixes, September 2026
--
-- 1. Widen columns that silently dropped rows on import.
--    Measured against Декомо/content_20_08_2026_22_51.xls (190 289 rows):
--      "Артикул поставщика" -> manufacturer_code      max 163  (was VARCHAR(128))
--      "Группа товара"      -> supplier_category      max 1673 (was VARCHAR(512))
--    31 146 rows failed with error 1406 "Data too long" because of this.
--
-- 2. Stop supplier_products deletes from destroying product<->supplier links.
--    product_supplier_links.supplier_product_id was ON DELETE CASCADE, so
--    deleting a supplier_products row deleted the link itself. The link is
--    identified by (product_id, supplier_id, supplier_sku), which survives a
--    re-import, so the reference to the raw row should just be cleared instead.
--    product_supplier_offers already uses ON DELETE SET NULL for the same field.
-- =============================================================================

USE svetoyar_staging;

-- 1. Column widths -----------------------------------------------------------

ALTER TABLE supplier_products
  MODIFY manufacturer_code      VARCHAR(255)  NULL,
  MODIFY supplier_category      VARCHAR(2048) NULL,
  MODIFY supplier_category_path VARCHAR(2048) NULL;

-- 2. Link foreign key --------------------------------------------------------

ALTER TABLE product_supplier_links
  DROP FOREIGN KEY fk_product_supplier_links_supplier_product;

ALTER TABLE product_supplier_links
  MODIFY supplier_product_id BIGINT UNSIGNED NULL;

ALTER TABLE product_supplier_links
  ADD CONSTRAINT fk_product_supplier_links_supplier_product
    FOREIGN KEY (supplier_product_id) REFERENCES supplier_products(id)
    ON DELETE SET NULL ON UPDATE CASCADE;
