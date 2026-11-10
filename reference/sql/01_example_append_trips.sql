-- Trainer example only. Students write their own cleaning SQL.
-- Copies the newly loaded rows from staging_trips into trips, then empties staging_trips.
-- Real cleaning steps (trim text, fix types, remove duplicates, ...) would come before this.
INSERT INTO trips SELECT * FROM staging_trips;
DELETE FROM staging_trips;
