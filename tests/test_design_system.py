"""ADR-19/ADR-20: the design-system plumbing — the {% icon %} tag, the base
layout's navigation state, the theme assets and /styleguide/."""

import pytest
from django.template import Context, Template, TemplateSyntaxError

from tests.factories import MembershipFactory

pytestmark = pytest.mark.django_db


def _render(source: str) -> str:
    return Template("{% load ui %}" + source).render(Context())


def test_icon_inlines_a_decorative_lucide_svg():
    html = _render('{% icon "bell" size=16 class="text-faint" %}')
    assert html.startswith("<svg")
    assert 'aria-hidden="true"' in html
    assert 'stroke-width="1.75"' in html
    assert 'width="16"' in html
    assert 'class="icon text-faint"' in html
    assert "<path" in html
    assert "license" not in html


def test_an_unknown_icon_fails_loudly():
    with pytest.raises(TemplateSyntaxError):
        _render('{% icon "no-such-icon" %}')


def test_initials():
    assert _render('{{ "Grace Nakato"|initials }}') == "GN"
    assert _render('{{ ""|initials }}') == "?"


def test_the_active_sidebar_item_is_marked_current(org_a, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    body = login_as(manager).get("/tasks/").content.decode()
    assert 'href="/tasks/" class="nav-item" aria-current="page"' in body
    assert 'href="/dashboard/" class="nav-item" aria-current' not in body


def test_pages_load_the_fonts_stylesheet_and_theme_script(org_a, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    body = login_as(manager).get("/tasks/my/").content.decode()
    assert "fonts.googleapis.com" in body
    assert "/static/theme.js" in body
    assert "/static/css/app.css" in body


def test_styleguide_renders_both_themes_for_members(org_a, login_as):
    staff = MembershipFactory(organisation=org_a, role="staff")
    response = login_as(staff).get("/styleguide/")
    body = response.content.decode()
    assert response.status_code == 200
    assert 'data-theme="light"' in body
    assert 'data-theme="dark"' in body
    components = ("btn-primary", "kanban-col", "task-card", "avatar", "table", "modal", "skeleton")
    for component in components:
        assert component in body


def test_styleguide_needs_login(client):
    assert client.get("/styleguide/").status_code == 302
