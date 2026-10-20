-- Cherry Move no longer stores transfers or phone hand-offs in Cherry Cloud (USB sticks, your own cloud or a NAS instead).
DROP TABLE IF EXISTS transfers;
DROP TABLE IF EXISTS pairs;
DELETE FROM blobs WHERE key LIKE 't/%';
