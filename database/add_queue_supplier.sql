-- The supplier, on the queue row itself.
--
-- Every view of the queue asks which supplier an item belongs to: the dashboard
-- groups by it, the filter selects by it, bulk moderation acts on it. The
-- answer lived two joins away, through supplier_products, and grouping 219,000
-- pending rows through that join took six seconds on every dashboard load.
--
-- It is derived, not new information - always supplier_products.supplier_id for
-- the row the queue item was raised from - so it is filled from there and kept
-- in step by the code that inserts queue items.

USE svetoyar_staging;

ALTER TABLE moderation_queue
  ADD COLUMN supplier_id INT UNSIGNED NULL COMMENT 'copied from the supplier row, for filtering'
    AFTER supplier_product_id,
  ADD KEY idx_moderation_queue_supplier (status, supplier_id),
  ADD CONSTRAINT fk_moderation_queue_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE SET NULL ON UPDATE CASCADE;

UPDATE moderation_queue mq
JOIN supplier_products sp ON sp.id = mq.supplier_product_id
SET mq.supplier_id = sp.supplier_id
WHERE mq.supplier_id IS NULL;
