"""Real passing test (a): GET /health returns 200."""


def test_health_returns_200(client):
    """Health endpoint must return 200 with status=ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_redirects_to_connections(client):
    """GET / must redirect to /connections."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/connections"
