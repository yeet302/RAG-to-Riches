import asyncio
from dataclasses import replace

import httpx
import pytest

from docs_agent.config import Settings
from docs_agent.discovery import official_url, parse_page
from docs_agent.main import DocsAgent

ROOT = 'https://opentelemetry.io/docs/demo/'


def agent(handler, **changes):
    settings = replace(Settings(paths=()), **changes)
    return DocsAgent(settings, docs_transport=httpx.MockTransport(handler))


def test_dynamic_discovery_both_sources():
    visited = []
    def handler(request):
        url = str(request.url)
        visited.append(url)
        if '/git/trees/' in url:
            return httpx.Response(200, json={'tree': [
                {'path': 'src/ad/docs/new-runbook.md', 'type': 'blob'},
                {'path': 'src/ad/Main.java', 'type': 'blob'},
                {'path': 'src/checkout/README.md', 'type': 'blob'}]})
        if '/contents/src/ad/docs/new-runbook.md' in url:
            return httpx.Response(200, text='Ad troubleshooting: inspect feature flags.')
        if url == ROOT:
            return httpx.Response(200, text='<nav>Navigation boilerplate</nav><main><h1>Demo</h1>'
                '<a href="services/ad/">Ad</a><a href="https://evil.example/">Other</a>'
                '<a href="/docs/collector/">Collector</a></main>')
        if url == ROOT + 'services/ad/':
            return httpx.Response(200, text='<div class="td-content"><h2 id="flags">Feature flags</h2>'
                '<p>Ad troubleshooting uses feature flags.</p><a href="../../">Back</a>'
                '<script>unwanted</script></div>')
        pytest.fail('Unexpected URL: ' + url)
    evidence, searched, errors = asyncio.run(agent(handler).retrieve('Ad troubleshooting feature flags'))
    assert not errors
    assert 'src/ad/docs/new-runbook.md' in searched
    assert ROOT + 'services/ad/' in searched
    assert any(item.link.endswith('#flags') for item in evidence)
    assert any('new-runbook.md#L' in item.link for item in evidence)
    assert all('unwanted' not in item.snippet and 'Navigation boilerplate' not in item.snippet for item in evidence)
    assert len(visited) == len(set(visited)) == 4


@pytest.mark.parametrize('href', ['https://evil.example/docs/demo/', '//evil.example/docs/demo/',
    '/docs/collector/', '/docs/demo/image.png', '/docs/demo/%2e%2e/private/',
    'https://opentelemetry.io:9999/docs/demo/', 'mailto:user@example.com'])
def test_out_of_scope_links(href):
    assert official_url(ROOT, href) is None


def test_canonical_urls():
    assert official_url(ROOT, 'services/ad/?utm=x#heading') == ROOT + 'services/ad/'
    assert official_url(ROOT, '/docs/demo') is None


def test_page_limit_is_reported():
    def handler(request):
        return httpx.Response(200, text='<main><p>Ad troubleshooting feature flags</p><a href="services/ad/">Ad</a></main>')
    evidence, searched, errors = asyncio.run(agent(handler, github_roots=(), max_web_pages=1).retrieve('Ad'))
    assert evidence and searched == [ROOT]
    assert any('page limit' in error for error in errors)


def test_failed_discovery_keeps_other_sources():
    def handler(request):
        if request.url.host == 'api.github.com':
            return httpx.Response(403)
        return httpx.Response(200, text='<main><p>Ad troubleshooting instructions</p></main>')
    evidence, searched, errors = asyncio.run(agent(handler).retrieve('troubleshooting'))
    assert evidence and searched == [ROOT]
    assert any('GitHub document discovery unavailable' in error for error in errors)


def test_timeout_preserves_completed_documents():
    async def handler(request):
        if str(request.url) == ROOT:
            return httpx.Response(200, text='<main><p>Ad troubleshooting feature flags</p><a href="slow/">Slow</a></main>')
        await asyncio.sleep(1)
        return httpx.Response(200)
    evidence, searched, errors = asyncio.run(agent(handler, github_roots=(), discovery_timeout=0.02).retrieve('Ad'))
    assert evidence and searched == [ROOT]
    assert any('time limit' in error for error in errors)


def test_sections_and_missing_body():
    docs, _ = parse_page('<nav>ignore</nav><main><h2 id="a">One</h2><p>First</p>'
                        '<h2 id="b">Two</h2><pre>command\nnext</pre></main>', ROOT)
    assert len(docs) == 2 and docs[0].link.endswith('#a') and docs[1].link.endswith('#b')
    assert 'command\nnext' in docs[1].text
    with pytest.raises(ValueError):
        parse_page('<nav>Not documentation</nav>', ROOT)


def test_truncated_tree_and_file_limit_reported():
    def handler(request):
        if '/git/trees/' in request.url.path:
            return httpx.Response(200, json={'truncated': True, 'tree': [
                {'path': 'src/ad/a.md', 'type': 'blob'}, {'path': 'src/ad/b.md', 'type': 'blob'}]})
        return httpx.Response(200, text='Ad instructions')
    evidence, searched, errors = asyncio.run(agent(handler, website_seeds=(), max_github_docs=1).retrieve('Ad'))
    assert evidence and len(searched) == 1
    assert len(errors) == 2
