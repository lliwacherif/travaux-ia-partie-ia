\echo === Row counts ===
SELECT 'trades' AS table_name, COUNT(*) AS rows FROM trades
UNION ALL
SELECT 'trade_services', COUNT(*) FROM trade_services;

\echo === Sample join (top 5 services for Plomberie / Sanitaires) ===
SELECT t.name AS trade,
       ts.designation,
       ts.unit,
       ts.category,
       ts.estimated_price
FROM   trade_services ts
JOIN   trades t ON t.id = ts.trade_id
WHERE  t.name = 'Plomberie / Sanitaires'
ORDER  BY ts.designation
LIMIT  5;

\echo === Orphan check (services with no matching trade) ===
SELECT COUNT(*) AS orphan_services
FROM   trade_services ts
LEFT   JOIN trades t ON t.id = ts.trade_id
WHERE  t.id IS NULL;

\echo === Services per trade (top 8) ===
SELECT t.name AS trade, COUNT(ts.id) AS services
FROM   trades t
LEFT   JOIN trade_services ts ON ts.trade_id = t.id
GROUP  BY t.name
ORDER  BY services DESC, t.name
LIMIT  8;
