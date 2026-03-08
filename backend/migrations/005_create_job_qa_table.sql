-- Create job_questions table.
-- Requires: jobs table.

CREATE TABLE IF NOT EXISTS job_questions (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    answered_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_questions_job_id ON job_questions(job_id);
