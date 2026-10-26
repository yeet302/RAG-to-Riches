"""Bounded, query-prioritized discovery of GitHub docs and official Demo pages."""
import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString, Tag


@dataclass
class Document:
    source: str
    text: str
    link: str
    github: bool = False
    offset: int = 0


def official_url(base, href):
    """Canonicalize only same-site Demo HTML pages; never follow external links."""
    parsed = urlsplit(urljoin(base, href))
    path = unquote(parsed.path)
    if (parsed.scheme != 'https' or parsed.netloc != 'opentelemetry.io'
            or not path.startswith('/docs/demo/') or '\\' in path
            or any(part in {'.', '..'} for part in path.split('/'))
            or PurePosixPath(path).suffix):
        return None
    return urlunsplit(('https', 'opentelemetry.io', path.rstrip('/') + '/', '', ''))


def parse_page(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    links = {link for tag in soup.find_all('a', href=True)
             if (link := official_url(url, tag['href']))}
    body = soup.select_one('.td-content') or soup.find('article') or soup.find('main')
    if body is None:
        raise ValueError('No documentation body')
    for tag in body.select('script, style, nav, footer, .td-page-meta, .feedback'):
        tag.decompose()
    documents, lines, anchor = [], [], ''
    offset = 0

    def flush():
        nonlocal offset
        if lines:
            link = url + ('#' + quote(anchor, safe='-_') if anchor else '')
            documents.append(Document(link, '\n'.join(lines), link, offset=offset))
            offset += len(lines)
            lines.clear()

    for node in body.descendants:
        if isinstance(node, Tag) and re.fullmatch(r'h[1-6]', node.name):
            flush()
            anchor = node.get('id', '')
        elif isinstance(node, NavigableString) and not isinstance(node, Comment) and node.parent.name not in {'script', 'style'}:
            lines.extend(line.strip() for line in str(node).splitlines() if line.strip())
    flush()
    return documents, links


async def discover(settings, question, client, terms):
    documents, searched, errors = [], [], []
    semaphore = asyncio.Semaphore(6)
    query = terms(question)

    async def read(url, **kwargs):
        async with semaphore:
            async with client.stream('GET', url, **kwargs) as response:
                response.raise_for_status()
                raw = bytearray()
                async for part in response.aiter_bytes():
                    raw.extend(part)
                    if len(raw) > 4_000_000:
                        raise ValueError('Source too large')
                return raw.decode('utf-8')

    def priority(value):
        return (-len(query & terms(value.replace('-', ' ').replace('/', ' '))), value)

    async def github():
        paths = set(settings.paths)
        if settings.github_roots:
            try:
                tree = json.loads(await read(
                    f'https://api.github.com/repos/{settings.repo}/git/trees/{quote(settings.ref, safe="")}',
                    params={'recursive': '1'}, headers={'Accept': 'application/vnd.github+json'}))
                if not isinstance(tree, dict) or not isinstance(tree.get('tree'), list):
                    raise ValueError('Invalid tree')
                if tree.get('truncated'):
                    errors.append('GitHub discovery tree was truncated; coverage is incomplete')
                for item in tree['tree']:
                    if not isinstance(item, dict):
                        continue
                    path = item.get('path', '')
                    if (item.get('type') == 'blob' and item.get('mode') != '120000'
                            and PurePosixPath(path).suffix.lower() in {'.md', '.rst', '.txt', '.adoc'}
                            and any(path.startswith(root.rstrip('/') + '/') for root in settings.github_roots)
                            and '..' not in PurePosixPath(path).parts):
                        paths.add(path)
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                errors.append('GitHub document discovery unavailable; configured files still searched')
        ordered = sorted(paths, key=priority)
        if len(ordered) > settings.max_github_docs:
            errors.append('GitHub document limit reached; coverage is incomplete')
        async def fetch(path):
            try:
                text = await read(f'https://api.github.com/repos/{settings.repo}/contents/{quote(path, safe="/")}',
                                  params={'ref': settings.ref}, headers={'Accept': 'application/vnd.github.raw+json'})
                if len(text.encode('utf-8')) > 1_000_000:
                    raise ValueError('Document too large')
                link = f'https://github.com/{settings.repo}/blob/{quote(settings.ref, safe="")}/{quote(path, safe="/")}'
                documents.append(Document(path, text, link, True))
                searched.append(path)
            except (httpx.HTTPError, ValueError):
                errors.append(f'{path}: documentation unavailable')
        await asyncio.gather(*(fetch(path) for path in ordered[:settings.max_github_docs]))

    async def website():
        pending, seen = set(settings.website_seeds), set()
        async def fetch(url):
            try:
                html = await read(url)
                docs, links = parse_page(html, url)
                if not docs:
                    raise ValueError('Empty documentation body')
                documents.extend(docs)
                searched.append(url)
                pending.update(links - seen)
            except (httpx.HTTPError, ValueError):
                errors.append(f'{url}: documentation unavailable')
        while pending and len(seen) < settings.max_web_pages:
            batch = sorted(pending - seen, key=priority)[:min(6, settings.max_web_pages - len(seen))]
            if not batch:
                break
            pending.difference_update(batch)
            seen.update(batch)
            await asyncio.gather(*(fetch(url) for url in batch))
        if pending - seen:
            errors.append('Official website page limit reached; coverage is incomplete')

    try:
        await asyncio.wait_for(asyncio.gather(github(), website()),
                               timeout=min(settings.discovery_timeout, settings.timeout * 0.45))
    except asyncio.TimeoutError:
        errors.append('Document discovery time limit reached; coverage is incomplete')
    return sorted(documents, key=lambda doc: doc.source), sorted(searched), sorted(set(errors))
