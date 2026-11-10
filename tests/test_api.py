import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.main import create_app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=create_app(Settings(env="test"))), base_url="http://test"
    ) as session:
        yield session


@pytest.mark.anyio
async def test_liveness_does_not_claim_business_readiness(client):
    assert (await client.get("/health/live")).json() == {
        "status": "alive",
        "milestone": "core_backend",
    }
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_NOT_CONFIGURED"
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    assert (await client.get("/api/v1/capabilities")).json()[
        "authentication"
    ] == "implemented_unconfigured"


@pytest.mark.anyio
async def test_request_id_is_sanitized(client):
    safe = await client.get("/health/live", headers={"X-Request-ID": "safe-id"})
    unsafe = await client.get("/health/live", headers={"X-Request-ID": "unsafe id"})
    assert safe.headers["X-Request-ID"] == "safe-id"
    assert unsafe.headers["X-Request-ID"] != "unsafe id"
    assert "cdn.jsdelivr.net" not in safe.headers["Content-Security-Policy"]
    docs = await client.get("/docs")
    assert docs.status_code == 200
    assert "https://cdn.jsdelivr.net" in docs.headers["Content-Security-Policy"]


@pytest.mark.anyio
async def test_no_unauthenticated_business_or_prediction_endpoints(client):
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert "/api/v1/auth/login" in paths and "/api/v1/tickets" in paths
    operation = paths["/api/v1/tickets"]["get"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert (await client.get("/api/v1/tickets")).status_code == 503


@pytest.mark.anyio
async def test_production_docs_hidden_and_paid_calls_cannot_be_enabled():
    async with AsyncClient(
        transport=ASGITransport(app=create_app(Settings(env="production"))), base_url="http://test"
    ) as client:
        response = await client.get("/docs")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "HTTP_ERROR"
        assert "cdn.jsdelivr.net" not in response.headers["Content-Security-Policy"]
    with pytest.raises(ValidationError, match="本地密钥和明确非零调用上限"):
        Settings(llm_enabled=True)


def test_example_empty_model_path_is_unconfigured(monkeypatch):
    monkeypatch.setenv("SMARTTICKET_CLASSIFIER_MANIFEST", "")
    assert Settings().classifier_manifest is None


def test_bailian_key_alias_and_project_key_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("SMARTTICKET_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    config = tmp_path / ".env"
    config.write_text("DASHSCOPE_API_KEY=MOCK_DASHSCOPE_ONLY\n", encoding="utf-8")
    settings = Settings(_env_file=config)
    assert settings.llm_api_key.get_secret_value() == "MOCK_DASHSCOPE_ONLY"
    assert settings.llm_model == "qwen-plus" and not settings.llm_enabled
    assert settings.llm_call_budget == 0
    assert "MOCK_DASHSCOPE_ONLY" not in repr(settings)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "MOCK_PROCESS_DASHSCOPE")
    monkeypatch.setenv("SMARTTICKET_LLM_API_KEY", "MOCK_PROJECT_BAILIAN")
    assert Settings().llm_api_key.get_secret_value() == "MOCK_PROJECT_BAILIAN"
