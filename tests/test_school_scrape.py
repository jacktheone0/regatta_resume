"""
The HS/College season scrape as a GitHub Actions job: the CLI entry point,
the orchestration over steps and sources, and the admin buttons that
dispatch the workflow instead of running a thread in the web process.
"""
from datetime import date

import pytest

import scraper_schools
from models import User


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def test_current_season_code():
    assert scraper_schools.current_season(date(2026, 10, 1)) == 'f26'
    assert scraper_schools.current_season(date(2026, 8, 1)) == 'f26'
    assert scraper_schools.current_season(date(2026, 7, 31)) == 's26'
    assert scraper_schools.current_season(date(2027, 2, 1)) == 's27'


@pytest.fixture
def fake_steps(monkeypatch):
    calls = []
    monkeypatch.setattr(scraper_schools, 'scrape_schools_only',
                        lambda source_type: calls.append(('schools', source_type, None)) or 3)
    monkeypatch.setattr(scraper_schools, 'scrape_rosters_for_season',
                        lambda season_code, source_type: calls.append(('rosters', source_type, season_code)) or 40)
    monkeypatch.setattr(scraper_schools, 'scrape_results_for_season',
                        lambda season_code, source_type: calls.append(('results', source_type, season_code)) or 500)
    return calls


def test_run_season_scrape_runs_every_step_for_every_source(fake_steps):
    outcome = scraper_schools.run_season_scrape(source='both', season='F26', step='all', log=lambda m: None)
    assert fake_steps == [
        ('schools', 'hs', None), ('schools', 'college', None),
        ('rosters', 'hs', 'f26'), ('rosters', 'college', 'f26'),
        ('results', 'hs', 'f26'), ('results', 'college', 'f26')]
    assert outcome[('results', 'college')] == 500


def test_run_season_scrape_one_step_one_source(fake_steps):
    scraper_schools.run_season_scrape(source='hs', season='s25', step='rosters', log=lambda m: None)
    assert fake_steps == [('rosters', 'hs', 's25')]


def test_run_season_scrape_defaults_to_current_season(fake_steps, monkeypatch):
    monkeypatch.setattr(scraper_schools, 'current_season', lambda today=None: 'f26')
    scraper_schools.run_season_scrape(source='college', step='results', log=lambda m: None)
    assert fake_steps == [('results', 'college', 'f26')]


def test_run_season_scrape_keeps_going_after_a_failing_source(fake_steps, monkeypatch):
    def boom(source_type):
        if source_type == 'hs':
            raise RuntimeError('site down')
        return 7
    monkeypatch.setattr(scraper_schools, 'scrape_schools_only', boom)
    outcome = scraper_schools.run_season_scrape(source='both', season='f26', step='schools', log=lambda m: None)
    assert outcome[('schools', 'hs')].startswith('error: site down')
    assert outcome[('schools', 'college')] == 7


def test_run_season_scrape_rejects_bad_arguments(fake_steps):
    with pytest.raises(ValueError):
        scraper_schools.run_season_scrape(season='2026')
    with pytest.raises(ValueError):
        scraper_schools.run_season_scrape(season='f26', source='middle-school')
    with pytest.raises(ValueError):
        scraper_schools.run_season_scrape(season='f26', step='teams')
    assert fake_steps == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_runs_and_reports(runner, fake_steps):
    result = runner.invoke(args=['scrape-schools', '--source', 'hs', '--season', 'f26', '--step', 'rosters'])
    assert result.exit_code == 0, result.output
    assert 'rosters  hs       -> 40' in result.output
    assert 'Season scrape complete.' in result.output


def test_cli_fails_when_a_step_fails(runner, fake_steps, monkeypatch):
    monkeypatch.setattr(scraper_schools, 'scrape_results_for_season',
                        lambda season_code, source_type: (_ for _ in ()).throw(RuntimeError('nope')))
    result = runner.invoke(args=['scrape-schools', '--source', 'hs', '--season', 'f26', '--step', 'results'])
    assert result.exit_code != 0
    assert 'error: nope' in result.output


def test_cli_rejects_unknown_step(runner, fake_steps):
    result = runner.invoke(args=['scrape-schools', '--step', 'teams'])
    assert result.exit_code != 0
    assert fake_steps == []


# ---------------------------------------------------------------------------
# Admin buttons dispatch the workflow
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = str(body or '')

    def json(self):
        return self._body


@pytest.fixture
def admin_client(app, client, db):
    user = User(email='admin@example.com', role='sailor', is_admin=True, email_verified=True)
    user.set_password('password123')
    db.session.add(user)
    db.session.commit()
    login = client.post('/login', data={'email': 'admin@example.com', 'password': 'password123'},
                        follow_redirects=True)
    assert login.status_code == 200
    return client


@pytest.fixture
def github(monkeypatch, app):
    """Capture the dispatch request instead of calling GitHub"""
    sent = {}
    monkeypatch.setitem(app.config, 'GITHUB_WORKFLOW_TOKEN', 'token-for-tests')
    # GitHub's own runners export GITHUB_WORKFLOW_REF / GITHUB_REPOSITORY,
    # which config.py would otherwise pick up when the suite runs in CI
    monkeypatch.setitem(app.config, 'GITHUB_WORKFLOW_REF', 'main')
    monkeypatch.setitem(app.config, 'GITHUB_REPO', 'jacktheone0/regatta_resume')

    def fake_post(url, headers=None, json=None, timeout=None):
        sent['url'] = url
        sent['json'] = json
        sent['auth'] = headers.get('Authorization')
        return FakeResponse(sent.get('status', 204))

    import app as app_module
    monkeypatch.setattr(app_module.requests, 'post', fake_post)
    return sent


def test_results_button_dispatches_workflow_with_inputs(admin_client, github):
    page = admin_client.post('/api/scraper/scrape-results', json={'season': 'F26', 'source': 'college'})
    assert page.status_code == 200, page.get_json()
    data = page.get_json()
    assert data['success'] and 'GitHub Actions' in data['message']
    assert data['actions_url'].endswith('/actions/workflows/scrape_schools.yml')
    assert github['url'].endswith('/actions/workflows/scrape_schools.yml/dispatches')
    assert github['json'] == {'ref': 'main', 'inputs': {'source': 'college', 'step': 'results', 'season': 'f26'}}
    assert github['auth'] == 'Bearer token-for-tests'


def test_schools_button_needs_no_season(admin_client, github):
    page = admin_client.post('/api/scraper/scrape-schools', json={'source': 'hs'})
    assert page.status_code == 200
    assert github['json']['inputs'] == {'source': 'hs', 'step': 'schools'}


def test_rosters_button_rejects_bad_season(admin_client, github):
    page = admin_client.post('/api/scraper/scrape-rosters', json={'season': '2026', 'source': 'hs'})
    assert page.status_code == 400
    assert 'url' not in github


def test_clubspot_button_still_dispatches_its_own_workflow(admin_client, github):
    page = admin_client.post('/api/scraper/run')
    assert page.status_code == 200
    assert github['url'].endswith('/actions/workflows/scrape.yml/dispatches')
    assert github['json'] == {'ref': 'main'}


def test_missing_token_explains_and_links(admin_client, github, monkeypatch, app):
    monkeypatch.setitem(app.config, 'GITHUB_WORKFLOW_TOKEN', None)
    page = admin_client.post('/api/scraper/scrape-rosters', json={'season': 'f26', 'source': 'hs'})
    assert page.status_code == 503
    assert 'GITHUB_WORKFLOW_TOKEN' in page.get_json()['error']
    assert page.get_json()['actions_url']


def test_github_refusal_is_reported(admin_client, github):
    github['status'] = 422
    page = admin_client.post('/api/scraper/scrape-rosters', json={'season': 'f26', 'source': 'hs'})
    assert page.status_code == 502
    assert 'HTTP 422' in page.get_json()['error']


def test_scrape_routes_need_an_admin(client, db, github):
    assert client.post('/api/scraper/scrape-results', json={'season': 'f26'}).status_code in (302, 401)
    user = User(email='sailor@example.com', role='sailor', is_admin=False, email_verified=True)
    user.set_password('password123')
    db.session.add(user)
    db.session.commit()
    client.post('/login', data={'email': 'sailor@example.com', 'password': 'password123'})
    assert client.post('/api/scraper/scrape-results', json={'season': 'f26'}).status_code == 403
    assert 'url' not in github
