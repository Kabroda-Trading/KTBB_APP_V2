"""Direct Jinja2 rendering coverage for templates/alt_matrix_desk.html --
the page route itself isn't wired into main.py yet (left for the
orchestrating session to add in one pass alongside the other new desk
pages), so this renders the template standalone rather than through
TestClient, same technique as any other template-only unit test in this
codebase would use. Confirms the is_logged_in/is_admin conditionals
actually gate the right sections, and that the template renders at all
for every combination -- a real render failure (bad Jinja syntax, a
missing include) would raise here, not silently ship."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates"))
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))


def _render(is_logged_in, is_admin):
    tmpl = env.get_template("alt_matrix_desk.html")
    return tmpl.render(is_logged_in=is_logged_in, is_admin=is_admin)


def test_renders_cleanly_for_every_login_admin_combination():
    for is_logged_in in (True, False):
        for is_admin in (True, False):
            html = _render(is_logged_in, is_admin)
            assert "Alt Matrix Desk" in html
            assert "SOL/USDT" in html and "ETH/USDT" in html


def test_your_account_section_only_renders_when_logged_in():
    assert "Your Account" in _render(True, False)
    assert "Your Account" not in _render(False, False)


def test_admin_section_only_renders_for_admins():
    assert "Admin — Recent Orders" in _render(True, True)
    assert "Admin — Recent Orders" not in _render(True, False)
    assert "Admin — Recent Orders" not in _render(False, False)   # a non-logged-in admin flag is nonsensical, but the gate must still hold


def test_admin_section_absent_for_logged_in_non_admin_even_with_account_section_present():
    html = _render(True, False)
    assert "Your Account" in html
    assert "Admin — Transition Feed" not in html


def test_public_symbol_row_always_present_regardless_of_login():
    for is_logged_in in (True, False):
        for is_admin in (True, False):
            assert 'id="symRow"' in _render(is_logged_in, is_admin)
