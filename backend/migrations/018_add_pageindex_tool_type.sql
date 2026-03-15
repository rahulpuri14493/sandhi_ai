-- Add PageIndex (vectorless RAG) to mcptooltype enum.
-- Idempotent: duplicate_object is ignored.

DO $$ BEGIN ALTER TYPE mcptooltype ADD VALUE 'pageindex'; EXCEPTION WHEN duplicate_object THEN null; END $$;
