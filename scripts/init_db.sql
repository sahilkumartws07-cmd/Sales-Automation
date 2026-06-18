DO
$$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'sales') THEN
      CREATE ROLE sales LOGIN PASSWORD 'sales';
   END IF;
END
$$;

SELECT 'CREATE DATABASE sales_automation OWNER sales'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sales_automation')\gexec

