"""organisations.tenancy: TenantScopedManager, save(), and the updated_seq
trigger. See CLAUDE.md "Tenancy" and docs/02-architecture.md §3.
"""

import pytest

from organisations.models import Location
from organisations.tenancy import TenantNotSet, tenant_context
from tests.factories import LocationFactory, OrganisationFactory

pytestmark = pytest.mark.django_db


def test_objects_raises_tenant_not_set_without_org():
    with pytest.raises(TenantNotSet):
        list(Location.objects.all())


def test_objects_filters_by_current_org():
    org_a = OrganisationFactory()
    org_b = OrganisationFactory()
    LocationFactory(organisation=org_a, name="A place")
    LocationFactory(organisation=org_b, name="B place")

    with tenant_context(org_a):
        names = list(Location.objects.values_list("name", flat=True))
    assert names == ["A place"]

    with tenant_context(org_b):
        names = list(Location.objects.values_list("name", flat=True))
    assert names == ["B place"]


def test_save_fills_organisation_from_context():
    org = OrganisationFactory()
    with tenant_context(org):
        location = Location(name="New place")
        location.save()
    assert location.organisation_id == org.id


def test_save_refuses_mismatched_organisation():
    org_a = OrganisationFactory()
    org_b = OrganisationFactory()
    location = Location(organisation=org_a, name="Mismatch")
    with tenant_context(org_b), pytest.raises(ValueError):
        location.save()


def test_new_instance_raises_without_context_or_organisation():
    location = Location(name="No org")
    with pytest.raises(TenantNotSet):
        location.save()


def test_unscoped_sees_every_organisation():
    org_a = OrganisationFactory()
    org_b = OrganisationFactory()
    LocationFactory(organisation=org_a)
    LocationFactory(organisation=org_b)
    assert Location.unscoped.count() == 2


def test_updated_seq_is_set_by_the_db_trigger():
    org = OrganisationFactory()
    location = LocationFactory(organisation=org)
    location.refresh_from_db()
    first_seq = location.updated_seq
    assert first_seq is not None

    location.name = "Renamed"
    location.save()
    location.refresh_from_db()
    assert location.updated_seq > first_seq


def test_updated_seq_increases_on_queryset_update():
    org = OrganisationFactory()
    location = LocationFactory(organisation=org)
    location.refresh_from_db()
    seq_before = location.updated_seq

    Location.unscoped.filter(pk=location.pk).update(name="Bulk renamed")
    location.refresh_from_db()
    assert location.updated_seq > seq_before


def test_the_project_imports_without_a_tenant_context():
    """Module-level code must never query a tenant-scoped manager: at import
    time (gunicorn start-up, `manage.py check`, an anonymous first request)
    there's no organisation, so `Model.objects` raises TenantNotSet. A fresh
    process running the system checks imports every URLconf, view and form."""
    import pathlib
    import subprocess
    import sys

    project_root = pathlib.Path(__file__).resolve().parents[2]
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "check"],
        cwd=str(project_root),
        env={
            "PATH": "/usr/bin:/bin",
            "DJANGO_SETTINGS_MODULE": "workflow.settings.dev",
            "ENV_FILE": "/nonexistent/workflow-test.env",
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
