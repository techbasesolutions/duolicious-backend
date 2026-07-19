-- Diagnosability + freshness for the search cache. The cache previously
-- recorded nothing about WHEN it was built, so "why is member X missing
-- from the map" was unanswerable post-hoc, and /map-markers served
-- arbitrarily stale snapshots (new members invisible to existing viewers
-- until they happened to change filters). built_at lets /map-markers
-- detect staleness and rebuild.
ALTER TABLE search_cache
  ADD COLUMN IF NOT EXISTS built_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
