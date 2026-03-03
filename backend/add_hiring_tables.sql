-- Migration script to create hiring_positions and agent_nominations tables
-- Run this if you have an existing database

-- Create hiring_status enum
DO $$ BEGIN
    CREATE TYPE hiringstatus AS ENUM ('open', 'closed', 'filled');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Create nominationstatus enum
DO $$ BEGIN
    CREATE TYPE nominationstatus AS ENUM ('pending', 'approved', 'rejected');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Create hiring_positions table
CREATE TABLE IF NOT EXISTS hiring_positions (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES users(id),
    title VARCHAR NOT NULL,
    description TEXT,
    requirements TEXT,
    status hiringstatus DEFAULT 'open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create agent_nominations table
CREATE TABLE IF NOT EXISTS agent_nominations (
    id SERIAL PRIMARY KEY,
    hiring_position_id INTEGER NOT NULL REFERENCES hiring_positions(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    developer_id INTEGER NOT NULL REFERENCES users(id),
    cover_letter TEXT,
    status nominationstatus DEFAULT 'pending',
    reviewed_by INTEGER REFERENCES users(id),
    reviewed_at TIMESTAMP,
    review_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_hiring_positions_business_id ON hiring_positions(business_id);
CREATE INDEX IF NOT EXISTS idx_hiring_positions_status ON hiring_positions(status);
CREATE INDEX IF NOT EXISTS idx_agent_nominations_position_id ON agent_nominations(hiring_position_id);
CREATE INDEX IF NOT EXISTS idx_agent_nominations_agent_id ON agent_nominations(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_nominations_developer_id ON agent_nominations(developer_id);
CREATE INDEX IF NOT EXISTS idx_agent_nominations_status ON agent_nominations(status);
