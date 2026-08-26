from agentsys.config import settings
from agentsys.tools import registry as registry_module


def _quiet_registry_build(monkeypatch):
    monkeypatch.setattr(settings, "enable_code_execution", False)
    monkeypatch.setattr(registry_module, "_register_mcp_tools", lambda _registry: None)


def test_knowledge_search_is_not_registered_without_service_token(monkeypatch):
    _quiet_registry_build(monkeypatch)
    monkeypatch.setattr(settings, "service_token", "", raising=False)

    registry = registry_module._build_registry()

    assert "knowledge_search" not in registry.names()
    assert "- knowledge_search:" not in registry.describe()


def test_knowledge_search_is_registered_with_service_token(monkeypatch):
    _quiet_registry_build(monkeypatch)
    monkeypatch.setattr(settings, "service_token", "test-service-token", raising=False)

    registry = registry_module._build_registry()

    assert "knowledge_search" in registry.names()
