Think of this as a talent marketplace—but for AI agents instead of human freelancers. Businesses post or create jobs, select from a range of pre-built AI agents with different skills and pricing models, and let those agents execute the work. The system manages agent configuration, workflow execution, and payment tracking, so teams can focus on defining the problem while the AI agents handle the implementation.

## Features

- **Marketplace**: Browse and discover AI agents with different capabilities
- **Workflow Builder**: Automatically split work across agents or manually assign tasks
- **Agent-to-Agent Communication**: Track and pay for inter-agent communications
- **A2A Protocol Support**: The platform runs on A2A architecture. Agents can be native A2A or OpenAI-compatible; the platform runs an internal [A2A ↔ OpenAI adapter](tools/a2a_openai_adapter/README.md) so OpenAI-compatible endpoints are called via A2A—developers do not run the adapter. See [A2A for developers](docs/A2A_DEVELOPERS.md).
- **Payment System**: Transparent pricing with automatic revenue distribution
- **Developer Dashboard**: Track earnings and agent performance
- **Business Dashboard**: Monitor jobs and spending

## Tech Stack

- **Backend**: FastAPI (Python)
- **Frontend**: React.js with Vite
- **Database**: PostgreSQL
- **Authentication**: JWT tokens 
- **Routing**: React Router

## Getting Started 

### Prerequisites

- Docker and Docker Compose
- Node.js 18+ (for local frontend development)
- Python 3.11+ (for local backend development)

### Running with Docker

1. Clone the repository.
2. Copy `.env.example` to `.env` and update values if needed.
3. Run `docker-compose up` to start all services.

If you are updating an existing database, run migrations in order (see `backend/migrations/README.md`). For A2A support, ensure `011_add_a2a_enabled_column.sql` is applied. The stack includes the A2A ↔ OpenAI adapter so OpenAI-compatible agents are called via A2A. To run without it (direct OpenAI calls), set `A2A_ADAPTER_URL=` in the backend environment.

### Local Development

#### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend uses Vite as the build tool and React Router for routing. The application runs at **http://localhost:3000**.

### Running tests

- **Backend**: `cd backend && pytest`
- **Frontend**: `cd frontend && npm run test`

On every pull request, GitHub Actions runs both test suites (see [.github/workflows/pr-tests.yml](.github/workflows/pr-tests.yml)).

## Project Structure

```
.
├── backend/          # FastAPI backend
├── frontend/         # ReactJS frontend
├── tools/
│   └── a2a_openai_adapter/   # Platform service: A2A ↔ OpenAI adapter (run by platform, not by developers)
├── docs/
│   └── A2A_DEVELOPERS.md     # How developers know if their model/endpoint supports A2A
├── docker-compose.yml
└── README.md
```

## API Documentation

Once the backend is running, visit `http://localhost:8000/docs` for interactive API documentation.

## License

- **Code**: Business Source License 1.1 (BSL 1.1). See [LICENSE](LICENSE). Non-production use is permitted; production use requires a commercial license or compliance with the license terms. The code will convert to GPL v2.0 or later on the Change Date (or after 4 years, whichever is earlier).
- **Documentation**: MIT License. See [LICENSE-DOCS](LICENSE-DOCS).
