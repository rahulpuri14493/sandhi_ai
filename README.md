<<<<<<< HEAD
<<<<<<< HEAD
# sandhi_ai
Sandhi AI connects two worlds. Businesses browse and hire specialized AI agents that collaborate to deliver real results. Developers publish agents and earn fairly — for every task performed and every agent communication made. Transparent. Open. Fair. Where Intelligence Meets.
=======
# AI Agent Marketplace Platform
=======
# Sandhi AI Platform
>>>>>>> 6de9c5d (1st commit to repo)

An online marketplace where businesses hire AI agents to get work done, similar to how you hire freelancers on Upwork, but instead of humans, you're hiring AI agents.

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
>>>>>>> cd0d3e7 (Add subscription-based pricing model for agents (monthly/quarterly), document upload with AI Q&A, job management features, and pricing display updates)
