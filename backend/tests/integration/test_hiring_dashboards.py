"""
Integration tests for Hiring + Dashboards endpoints.

Uses in-memory SQLite and TestClient (integration_client fixture).
Avoids external services; only exercises DB + route logic.
"""

from fastapi.testclient import TestClient

from models.agent import AgentStatus


def _auth_headers(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


class TestHiringDashboards:
    def test_hiring_position_nomination_review_and_dashboards(
        self,
        integration_client: TestClient,
        business_user,
        developer_user,
        sample_agent,
    ):
        biz_headers = _auth_headers(business_user)
        dev_headers = _auth_headers(developer_user)

        # Business creates a hiring position
        r = integration_client.post(
            "/api/hiring/positions",
            json={"title": "Backend Dev", "description": "Build APIs", "requirements": "FastAPI"},
            headers=biz_headers,
        )
        assert r.status_code == 201, r.text
        position = r.json()
        position_id = position["id"]
        assert position["title"] == "Backend Dev"

        # List positions (defaults to OPEN)
        r2 = integration_client.get("/api/hiring/positions", headers=biz_headers)
        assert r2.status_code == 200
        assert any(p["id"] == position_id for p in r2.json())

        # Get position detail (with nominations)
        r3 = integration_client.get(f"/api/hiring/positions/{position_id}", headers=biz_headers)
        assert r3.status_code == 200
        body = r3.json()
        assert body["id"] == position_id
        assert "nominations" in body

        # Developer nominates their agent
        r4 = integration_client.post(
            "/api/hiring/nominations",
            json={
                "hiring_position_id": position_id,
                "agent_id": sample_agent.id,
                "cover_letter": "Pick me",
            },
            headers=dev_headers,
        )
        assert r4.status_code == 201, r4.text
        nomination = r4.json()
        nomination_id = nomination["id"]
        assert nomination["agent_id"] == sample_agent.id

        # Business lists nominations (scoped to their positions)
        r5 = integration_client.get("/api/hiring/nominations", headers=biz_headers)
        assert r5.status_code == 200
        assert any(n["id"] == nomination_id for n in r5.json())

        # Business approves nomination (activates agent)
        r6 = integration_client.put(
            f"/api/hiring/nominations/{nomination_id}/review",
            json={"status": "approved", "review_notes": "ok"},
            headers=biz_headers,
        )
        assert r6.status_code == 200, r6.text
        assert r6.json()["status"] == "approved"

        # Agent should now be ACTIVE
        r7 = integration_client.get("/api/agents", headers=dev_headers)
        assert r7.status_code == 200
        agents = r7.json()
        match = [a for a in agents if a["id"] == sample_agent.id]
        assert match
        assert match[0]["status"] == AgentStatus.ACTIVE.value

        # Dashboards: business baseline
        r8 = integration_client.get("/api/businesses/jobs", headers=biz_headers)
        assert r8.status_code == 200
        assert isinstance(r8.json(), list)

        r9 = integration_client.get("/api/businesses/spending", headers=biz_headers)
        assert r9.status_code == 200
        assert "total_spent" in r9.json()
        assert "job_count" in r9.json()

        # Dashboards: developer baseline
        r10 = integration_client.get("/api/developers/stats", headers=dev_headers)
        assert r10.status_code == 200
        assert "agent_count" in r10.json()

        r11 = integration_client.get("/api/developers/agents", headers=dev_headers)
        assert r11.status_code == 200
        assert isinstance(r11.json(), list)

        r12 = integration_client.get("/api/developers/earnings", headers=dev_headers)
        assert r12.status_code == 200
        assert "total_earnings" in r12.json()

