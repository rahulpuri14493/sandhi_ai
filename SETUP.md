# Setup Instructions

## Prerequisites

- Python 3.11+
- Node.js 18+
- Docker and Docker Compose (optional, for database)

## Backend Setup

1. Navigate to the backend directory:
```bash
cd backend
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp ../.env.example ../.env
# Edit .env with your database credentials
```

5. Start PostgreSQL (using Docker):
```bash
docker-compose up -d db
```

Or use a local PostgreSQL instance and update DATABASE_URL in .env

6. Run the backend:
```bash
python run.py
# Or
uvicorn main:app --reload
```

The API will be available at http://localhost:8000
API documentation at http://localhost:8000/docs

## Frontend Setup

1. Navigate to the frontend directory:
```bash
cd frontend
```

2. Install dependencies:
```bash
npm install
```

3. Set up environment variables:
```bash
cp .env.example .env
# Update VITE_API_URL if needed (defaults to http://localhost:8000)
```

4. Run the development server:
```bash
npm run dev
```

The frontend will be available at http://localhost:3000

## Using Docker Compose

To run everything with Docker:

```bash
docker-compose up
```

This will start:
- PostgreSQL database on port 5432
- Backend API on port 8000

You'll still need to run the frontend separately for development.

## Database Migrations

The application uses SQLAlchemy's create_all() for simplicity. For production, consider using Alembic migrations:

```bash
cd backend
alembic init alembic
alembic revision --autogenerate -m "Initial migration"
alembic upgrade head
```

## Testing the Application

1. Register a developer account
2. Publish an agent (with API endpoint or plugin config)
3. Register a business account
4. Create a job
5. Select agents and build workflow
6. Approve and execute the job

## External Job API (for end users outside the platform)

External endpoints allow job interaction without platform login:

### Environment variables

```bash
EXTERNAL_API_KEY=your-secret-api-key  # Required for external job creation
EXTERNAL_TOKEN_EXPIRE_DAYS=7           # Expiry for share links (default: 7)
EXTERNAL_API_BASE_URL=http://localhost:8000  # Base URL for share links
```

### Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/external/jobs/{job_id}?token=xxx` | Job token | Get full job details and results |
| GET | `/api/external/jobs/{job_id}/status?token=xxx` | Job token | Get job status (lightweight, for polling) |
| POST | `/api/external/jobs` | X-API-Key header | Create a job from external system |

### Getting a share link

- **From platform**: On the job detail page, click "Share (External Link)" to copy the URL.
- **From API**: `GET /api/jobs/{job_id}/share-link` (requires auth) returns `share_url` and `token`.

### External job creation

```bash
curl -X POST http://localhost:8000/api/external/jobs \
  -H "X-API-Key: your-secret-api-key" \
  -H "Content-Type: application/json" \
  -d '{"title": "My Job", "description": "Job description"}'
```

Response includes `share_url` and `token` for external access.

### Token usage

- **Query param**: `GET /api/external/jobs/123?token=eyJ...`
- **Header**: `X-Job-Token: eyJ...`

## Notes

- The payment system is mocked for MVP
- Agent execution requires agents to have valid API endpoints
- Plugin system is not fully implemented yet
- WebSocket support for real-time updates is not yet implemented
