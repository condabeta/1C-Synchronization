-- Credit line for suppliers whose photos and descriptions we take from their
-- own site. LED CRYSTAL granted permission on 2026-09-05 on condition the
-- source is credited (docs/supplier_permissions.md), so any product card built
-- from their pages has to carry it.
--
-- Held per supplier rather than per product: it is one string, and products
-- already point at their primary supplier.

USE svetoyar_staging;

ALTER TABLE suppliers
  ADD COLUMN content_attribution VARCHAR(255) NULL
    COMMENT 'credit line to show on product cards using this supplier content';

UPDATE suppliers
SET content_attribution = 'Фото и описание — LED CRYSTAL (led-crystal.ru)'
WHERE code = 'crystal';
