-- Add sync protection configuration
-- This allows protecting manually entered fields from being overwritten during supplier imports

-- Table to configure sync settings per supplier and field
USE svetoyar_staging;

CREATE TABLE IF NOT EXISTS supplier_field_sync_config (
  id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
  supplier_id     INT UNSIGNED    NOT NULL,
  field_name      VARCHAR(64)     NOT NULL COMMENT 'e.g., description, price, stock_qty',
  sync_enabled    TINYINT(1)      NOT NULL DEFAULT 1 COMMENT '1=sync from supplier, 0=manual only',
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_supplier_field_sync (supplier_id, field_name),
  CONSTRAINT fk_supplier_field_sync_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add product-level sync override flag to products table
ALTER TABLE products
ADD COLUMN sync_override_enabled TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1=allow all field sync, 0=respect supplier config';

-- Insert default sync configuration based on client requirements
-- Protected fields (manual-only): description, attributes
-- Sync fields (auto-update): price, stock_qty, is_available

INSERT IGNORE INTO supplier_field_sync_config (supplier_id, field_name, sync_enabled)
SELECT id, 'description', 0 FROM suppliers
UNION ALL
SELECT id, 'price', 1 FROM suppliers  
UNION ALL
SELECT id, 'price_retail', 1 FROM suppliers
UNION ALL
SELECT id, 'price_old', 1 FROM suppliers
UNION ALL
SELECT id, 'stock_qty', 1 FROM suppliers
UNION ALL
SELECT id, 'is_available', 1 FROM suppliers
UNION ALL
SELECT id, 'name', 0 FROM suppliers
UNION ALL
SELECT id, 'brand', 0 FROM suppliers
UNION ALL
SELECT id, 'images_json', 0 FROM suppliers;
