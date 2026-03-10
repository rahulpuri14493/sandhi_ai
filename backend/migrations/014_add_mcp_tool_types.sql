-- Add industry-standard MCP tool types to mcptooltype enum.
-- Idempotent: duplicate_object is ignored.

DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'pinecone'; EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'weaviate'; EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'qdrant'; EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'chroma'; EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'mysql'; EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'elasticsearch'; EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 's3'; EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'slack'; EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'github'; EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'notion'; EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'rest_api'; EXCEPTION WHEN duplicate_object THEN null; END $$;
