-- Supplier content we must not publish.
--
-- On 23.09.2026 a supplier warned that customers were receiving pre-court
-- claims over the photograph of one product - EV_a044057, «Гирлянда Eurosvet
-- занавес 2*3м IP65 200-101 белый» - and asked everyone to take that picture
-- down. The photograph had reached us through Dekomo's feed like any other.
--
-- Hiding the product is not enough, and neither is deleting the image rows: the
-- next import brings them straight back, because the feed still carries them.
-- So a block is recorded here, and the import respects it - the row still
-- imports, with its price and stock, but without the content that is disputed.
--
-- `scope` says how much is blocked:
--   images  - the row imports, its pictures do not
--   product - the row is not imported at all
--
-- Blocks are by supplier and article, because that is what stays stable across
-- imports, and they are never removed automatically: a claim is withdrawn by a
-- person, not by a feed.

USE svetoyar_staging;

CREATE TABLE IF NOT EXISTS blocked_content (
  id           INT UNSIGNED NOT NULL AUTO_INCREMENT,
  supplier_id  INT UNSIGNED NOT NULL,
  supplier_sku VARCHAR(128) NOT NULL,
  scope        ENUM('images', 'product') NOT NULL DEFAULT 'images',
  reason       VARCHAR(512) NULL COMMENT 'why, in words a lawyer would recognise',
  source       VARCHAR(255) NULL COMMENT 'who told us, and when',
  blocked_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_blocked_content (supplier_id, supplier_sku, scope),
  CONSTRAINT fk_blocked_content_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO blocked_content (supplier_id, supplier_sku, scope, reason, source)
SELECT id, 'EV_a044057', 'images',
       'Досудебная претензия по фотографии. Поставщик просил снять снимок со всех сайтов; оригинал у Декомо уже удалён (404).',
       'Письмо поставщика через клиента, 23.09.2026'
FROM suppliers WHERE code = 'dekomo'
ON DUPLICATE KEY UPDATE reason = VALUES(reason), source = VALUES(source);
