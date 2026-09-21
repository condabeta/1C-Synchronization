-- Indexes the moderation queue needs once it holds the whole catalogue.
--
-- The queue page sorts by (priority, created_at) inside a status and pages
-- through the result. With 219,000 pending rows and only an index on `status`,
-- MySQL read every one of them and sorted the lot to return twenty: the page
-- took 23 seconds, and the dashboard's aggregates on top of it made it worse.
--
-- The composite index gives the sort for free, and lets the count that the
-- unfiltered page needs be answered from the index alone.

USE svetoyar_staging;

CREATE INDEX idx_moderation_queue_work
  ON moderation_queue (status, priority, created_at, id);

-- The queue joins to the supplier row for the supplier filter and the article.
CREATE INDEX idx_moderation_queue_supplier_product
  ON moderation_queue (supplier_product_id, status);
