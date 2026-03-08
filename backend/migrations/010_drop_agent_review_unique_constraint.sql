-- Allow multiple reviews per user per agent (drop one-review-per-user constraint).
-- Run after 009. Fixes: duplicate key value violates unique constraint "uq_agent_review_user"

ALTER TABLE agent_reviews DROP CONSTRAINT IF EXISTS uq_agent_review_user;
