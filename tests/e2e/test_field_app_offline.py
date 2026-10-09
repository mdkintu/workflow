"""End to end, in a real browser: the field app works offline and syncs
back exactly once (docs/01-requirements.md F1.3, F2.3, F4.x comments,
NFR-O1/O5/O7). Run with `pytest -m e2e`.

The phone is Playwright's Chromium at a small screen size; "offline" is
Playwright's network emulation, which the service worker sees too.
"""

import io
import os
from datetime import timedelta

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from PIL import Image

from checklists.models import ChecklistRun, ChecklistRunItemTick
from organisations.tenancy import tenant_context
from sync import pull
from sync.models import OfflineSyncLog
from tasks.models import Task, TaskComment, TaskPhoto
from tests.factories import (
    ChecklistRunFactory,
    ChecklistRunItemFactory,
    LocationFactory,
    MembershipFactory,
    OrganisationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    TaskFactory,
)

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# Playwright's sync API runs an event loop in this thread; the ORM calls
# below are ordinary blocking test code.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture(autouse=True)
def _no_lag(monkeypatch):
    """The pull holds back rows younger than LAG (ADR-03); this test's rows
    are all seconds old. The live server runs in this process."""
    monkeypatch.setattr(pull, "LAG", timedelta(0))


@pytest.fixture
def world():
    org = OrganisationFactory(name="Demo Guest House")
    now = timezone.now()
    with tenant_context(org):
        main = LocationFactory(organisation=org, name="Main building")
        shift = ShiftFactory(organisation=org, location=main, name="Morning")
        peter = MembershipFactory(organisation=org, role="staff", user__name="Peter")
        ShiftAssignmentFactory(
            organisation=org, shift=shift, membership=peter, date=timezone.localdate()
        )
        task = TaskFactory(
            organisation=org,
            location=main,
            title="Clean Room 12",
            assignee_location=False,
            assignee_membership=peter,
            photo_required=True,
            due_at=now + timedelta(hours=1),
        )
        run = ChecklistRunFactory(
            organisation=org,
            location=main,
            shift=shift,
            shift_date=timezone.localdate(),
            name="Opening checklist",
            occurrence_start=now - timedelta(minutes=5),
            due_at=now + timedelta(hours=1),
        )
        ChecklistRunItemFactory(organisation=org, run=run, order=1, label="Unlock stores")
        ChecklistRunItemFactory(organisation=org, run=run, order=2, label="Ice machine on")
    return org, peter, task, run


@pytest.fixture
def phone(browser, live_server, world):
    _org, peter, _task, _run = world
    client = Client()
    client.force_login(peter.user)
    session = client.session
    session["active_membership_id"] = str(peter.id)
    session.save()
    context = browser.new_context(viewport={"width": 360, "height": 740}, base_url=live_server.url)
    context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    page = context.new_page()
    yield context, page
    context.close()


def _jpeg(tmp_path):
    path = tmp_path / "proof.jpg"
    buffer = io.BytesIO()
    Image.new("RGB", (1600, 1200), (180, 120, 60)).save(buffer, "JPEG", quality=95)
    path.write_bytes(buffer.getvalue())
    return path


def _open_online(page):
    page.goto("/app/")
    page.get_by_text("Clean Room 12").wait_for()
    page.get_by_test_id("sync-badge").filter(has_text="Synced").wait_for()
    # The service worker is active and has the shell before we go offline.
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    page.wait_for_function("caches.match('/app/').then((r) => !!r)")


def test_f1_3_f2_3_work_done_offline_survives_a_reload_and_syncs_once(phone, world, tmp_path):
    org, peter, task, run = world
    context, page = phone
    _open_online(page)

    # --- no signal ---------------------------------------------------------
    context.set_offline(True)
    page.reload()
    page.get_by_text("Clean Room 12").wait_for()  # from the local store
    page.get_by_test_id("net").filter(has_text="Offline").wait_for()

    # F1.3: photo + done
    page.get_by_text("Clean Room 12").click()
    page.get_by_test_id("task-photo").set_input_files(str(_jpeg(tmp_path)))
    page.get_by_role("button", name="Mark done").click()
    page.get_by_text("waiting to sync").wait_for()

    # F4: a comment written offline
    page.get_by_placeholder("Write a comment…").fill("Sheets changed")
    page.get_by_role("button", name="Send").click()
    page.get_by_test_id("comment").filter(has_text="Sheets changed").wait_for()

    # F2.3: tick one item, skip the other with a reason
    page.goto("/app/#/run/" + str(run.id))
    unlock = page.get_by_test_id("run-item").filter(has_text="Unlock stores")
    ice = page.get_by_test_id("run-item").filter(has_text="Ice machine on")
    unlock.get_by_role("button", name="Done").click()
    unlock.get_by_role("button", name="Done").wait_for(state="detached")
    ice.get_by_role("button", name="Skip…").click()
    ice.get_by_placeholder("Why?").fill("Machine broken")
    ice.get_by_role("button", name="Skip", exact=True).click()
    ice.get_by_text("Machine broken").wait_for()

    # NFR-O5: still queued after the app is closed and reopened offline
    page.reload()
    page.get_by_test_id("sync-badge").filter(has_text="waiting").wait_for()
    with tenant_context(org):
        assert Task.objects.get(pk=task.pk).status == "pending"
        assert not OfflineSyncLog.objects.exists()

    # --- signal is back ----------------------------------------------------
    context.set_offline(False)
    # The emulation doesn't fire the `online` event a phone's OS would.
    page.evaluate("dispatchEvent(new Event('online'))")
    page.get_by_test_id("sync-badge").filter(has_text="Synced").wait_for(timeout=20_000)

    with tenant_context(org):
        task.refresh_from_db()
        assert task.status == "done"
        assert task.completed_by == peter
        assert TaskPhoto.objects.filter(task=task, linked_at__isnull=False).count() == 1
        assert TaskComment.objects.filter(task=task, body="Sheets changed").count() == 1
        assert ChecklistRun.objects.get(pk=run.pk).status == "flagged"  # a skipped item
        assert ChecklistRunItemTick.objects.filter(run_item__run=run).count() == 2
        logged = OfflineSyncLog.objects.count()
        assert logged == 4  # complete, comment, tick, skip

    # NFR-O7: syncing again sends nothing twice
    page.get_by_role("link", name="Me").click()
    page.get_by_test_id("sync-now").click()
    page.get_by_test_id("sync-badge").filter(has_text="Synced").wait_for()
    with tenant_context(org):
        assert OfflineSyncLog.objects.count() == logged
        assert TaskComment.objects.filter(task=task).count() == 1


def test_changes_from_the_server_arrive_and_removed_work_disappears(phone, world):
    org, peter, task, _run = world
    context, page = phone
    _open_online(page)
    with tenant_context(org):
        Task.objects.filter(pk=task.pk).update(title="Clean Room 14")
        other = MembershipFactory(organisation=org, role="staff")
        TaskFactory(
            organisation=org,
            location=task.location,
            title="Fold towels",
            assignee_location=False,
            assignee_membership=peter,
        )
        # A reassignment away from Peter becomes a tombstone for him.
        extra = TaskFactory(
            organisation=org,
            location=task.location,
            title="Mop hall",
            assignee_location=False,
            assignee_membership=peter,
        )
    page.get_by_role("link", name="Me").click()
    page.get_by_test_id("sync-now").click()
    page.get_by_role("link", name="Tasks").click()
    page.get_by_text("Mop hall").wait_for()
    with tenant_context(org):
        Task.objects.filter(pk=extra.pk).update(assignee_membership=other)
    page.get_by_role("link", name="Me").click()
    page.get_by_test_id("sync-now").click()
    page.get_by_role("link", name="Tasks").click()
    page.get_by_text("Clean Room 14").wait_for()
    page.get_by_text("Fold towels").wait_for()
    page.get_by_text("Mop hall").wait_for(state="detached")
