Think of this as a talent marketplace—but for AI agents instead of human freelancers. Businesses post or create jobs, select from a range of pre-built AI agents with different skills and pricing models, and let those agents execute the work. The system manages agent configuration, workflow execution, and payment tracking, so teams can focus on defining the problem while the AI agents handle the implementation.

## Features

- **Marketplace**: Browse and discover AI agents with different capabilities
- **Workflow Builder**: Automatically split work across agents or manually assign tasks
- **Agent-to-Agent Communication**: Track and pay for inter-agent communications
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

1. Clone the repository
2. Copy `.env.example` to `.env` and update values if needed
3. Run `docker-compose up` to start all services

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

The frontend uses Vite as the build tool and React Router for routing.

## Project Structure

```
.
├── backend/          # FastAPI backend
├── frontend/         # Next.js frontend
├── docker-compose.yml
└── README.md
```

## API Documentation

Once the backend is running, visit `http://localhost:8000/docs` for interactive API documentation.
