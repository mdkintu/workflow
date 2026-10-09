"""Cross-tenant leak tests (ADR-05, docs/02-architecture.md §3): every
concrete TenantModel subclass must be isolated by organisation, and must
have a factory registered so new models are covered automatically.
"""

import inspect

import pytest
from django.apps import apps
from factory.django import DjangoModelFactory

from organisations.tenancy import TenantModel, tenant_context
from tests import factories as factories_module
from tests.factories import LocationFactory, MembershipFactory


def _discover_tenant_models() -> list[type[TenantModel]]:
    return [
        model
        for model in apps.get_models()
        if issubclass(model, TenantModel) and not model._meta.abstract
    ]


def _discover_factory_registry() -> dict[type, type[DjangoModelFactory]]:
    registry: dict[type, type[DjangoModelFactory]] = {}
    for _name, obj in inspect.getmembers(factories_module, inspect.isclass):
        if not issubclass(obj, DjangoModelFactory):
            continue
        model = obj._meta.model
        if model is not None:
            registry[model] = obj
    return registry


TENANT_MODELS = _discover_tenant_models()
FACTORY_REGISTRY = _discover_factory_registry()


def test_every_tenant_model_has_a_registered_factory():
    missing = sorted(m.__name__ for m in TENANT_MODELS if m not in FACTORY_REGISTRY)
    assert not missing, (
        f"Add a factory in tests/factories.py for: {missing} "
        "(every TenantModel subclass needs a leak test + a factory)."
    )


@pytest.mark.django_db
@pytest.mark.parametrize("model", TENANT_MODELS, ids=lambda m: m.__name__)
def test_cross_tenant_leak(model, org_a, org_b):
    factory_cls = FACTORY_REGISTRY.get(model)
    if factory_cls is None:
        pytest.fail(f"No factory registered for {model.__name__}")

    factory_cls(organisation=org_a)

    with tenant_context(org_b):
        assert model.objects.count() == 0
    with tenant_context(org_a):
        assert model.objects.count() == 1


@pytest.mark.django_db
def test_dashboard_view_does_not_leak_locations_across_orgs(client, org_a, org_b, login_as):
    LocationFactory(organisation=org_a, name="Org A Secret Location")
    LocationFactory(organisation=org_b, name="Org B Own Location")
    manager = MembershipFactory(organisation=org_b, role="manager")

    login_as(manager)
    response = client.get("/dashboard/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "Org A Secret Location" not in content
    assert "Org B Own Location" in content
