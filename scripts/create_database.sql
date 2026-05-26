-- Create the local development database for travaux-ia.
-- Meant to be executed against the default 'postgres' database as the
-- liwa-travauxia role (which is a superuser in this local setup).
CREATE DATABASE travauxia_devis
    WITH OWNER = "liwa-travauxia"
         ENCODING = 'UTF8';
