-- Remove rows the test suite wrote into data/app.db on 2026-07-24 ~21:57 UTC.
--
-- Cause: switching write routes to get_db_write while the `client` test fixture
-- still only overrode get_db, so write routes reached the application engine.
-- Fixed in commit 617e927; this only cleans up what already landed.
--
-- Safe boundary: real data in this database ends 2026-07-16. Everything after
-- '2026-07-24 21:50' is fixture data ("a", "b", "Firewall", "Still alive", ...).
-- Backup taken first: data/backups/app-20260724-145818.db
--
-- Run with:  sqlite3 data/app.db < cleanup_test_rows.sql
-- Expected:  tasks 115, projects 5, edges 29, newest task 2026-07-16

PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TEMP TABLE t_tasks AS
    SELECT id FROM tasks WHERE created_at > '2026-07-24 21:50';
CREATE TEMP TABLE t_projects AS
    SELECT id FROM projects WHERE created_at > '2026-07-24 21:50';

DELETE FROM task_dependencies
 WHERE task_id IN (SELECT id FROM t_tasks)
    OR depends_on_task_id IN (SELECT id FROM t_tasks);

DELETE FROM activity_events WHERE created_at > '2026-07-24 21:50';

-- Clear self- and project-FKs before the delete so no row transiently dangles.
UPDATE tasks SET parent_task_id = NULL WHERE id IN (SELECT id FROM t_tasks);
UPDATE tasks SET deleted_with_project_id = NULL WHERE id IN (SELECT id FROM t_tasks);

DELETE FROM tasks WHERE id IN (SELECT id FROM t_tasks);
DELETE FROM projects WHERE id IN (SELECT id FROM t_projects);

COMMIT;

PRAGMA foreign_key_check;

SELECT 'tasks', COUNT(*) FROM tasks
UNION ALL SELECT 'projects', COUNT(*) FROM projects
UNION ALL SELECT 'edges', COUNT(*) FROM task_dependencies
UNION ALL SELECT 'newest_task', MAX(created_at) FROM tasks;
