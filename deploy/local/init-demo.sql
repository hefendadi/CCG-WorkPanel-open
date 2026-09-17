-- Fictional users and isolated local database only.
CREATE USER 'demo_migrate'@'%' IDENTIFIED BY 'demo-migrate-local-only';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES, TRIGGER
ON ccg_public_demo.* TO 'demo_migrate'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON ccg_public_demo.* TO 'demo_app'@'%';
