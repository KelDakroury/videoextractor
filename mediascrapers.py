#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2018 Elvis Yu-Jing Lin <elvisyjlin@gmail.com>
# Licensed under the MIT License - https://opensource.org/licenses/MIT

import json
import os
import sys
import time
import re
from abc import ABCMeta, abstractmethod
from html.parser import HTMLParser
import urllib.parse
import urllib.request

try:
    from bs4 import BeautifulSoup as bs
except ImportError:
    bs = None

try:
    import requests
except ImportError:
    requests = None

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable):
        return iterable

from util.file import get_basename, get_extension, rename_file, safe_filename, safe_makedirs
from util.media import mux_audio_video
from util.network import open_public_url
from util.url import get_filename, complete_url, download, is_media, is_video

MEDIA_ATTR_NAMES = {
    'content',
    'data-bg',
    'data-image',
    'data-lazy',
    'data-original',
    'data-poster',
    'data-retina',
    'data-src',
    'href',
    'poster',
    'src',
}

CSS_URL_RE = re.compile(r'url\((["\']?)([^)"\']+)\1\)', re.IGNORECASE)
IFRAME_SRC_RE = re.compile(r'<iframe[^>]+src="([^"]+)"', re.IGNORECASE)
PLAYER_CONFIG_RE = re.compile(r'window\.playerConfig\s*=\s*({.*?})</script>', re.S)
VIDEO_EMBED_ATTR_NAMES = {'src', 'data-src', 'href'}
VIDEO_EMBED_HOST_HINTS = (
    'youtube.com',
    'youtu.be',
    'vimeo.com',
    'wistia',
    'brightcove',
    'ypo.education',
)
VIDEO_EMBED_PATH_HINTS = (
    '/embed/',
    '/video/',
    'embed=',
    'player',
)
MAX_SAFE_PAGE_BYTES = 8 * 1024 * 1024


def _add_media_candidate(media_urls, seen, value):
    if value is None:
        return
    value = value.strip()
    if value == '':
        return
    if is_media(value) and value not in seen:
        seen.add(value)
        media_urls.append(value)


def _extract_srcset_urls(value):
    urls = []
    for part in value.split(','):
        candidate = part.strip().split(' ')[0].strip()
        if candidate != '':
            urls.append(candidate)
    return urls


def _collect_media_from_attrs(media_urls, seen, attrs):
    for name, value in attrs.items():
        if value is None:
            continue
        name = name.lower()
        if name in MEDIA_ATTR_NAMES:
            _add_media_candidate(media_urls, seen, value)
        elif name.endswith('srcset'):
            for url in _extract_srcset_urls(value):
                _add_media_candidate(media_urls, seen, url)
        elif name == 'style':
            for _, url in CSS_URL_RE.findall(value):
                _add_media_candidate(media_urls, seen, url)


def _collect_media_from_source(media_urls, seen, source):
    for _, url in CSS_URL_RE.findall(source):
        _add_media_candidate(media_urls, seen, url)


def _is_video_embed_url(url):
    lowered = url.lower()
    return any(hint in lowered for hint in VIDEO_EMBED_HOST_HINTS) or any(
        hint in lowered for hint in VIDEO_EMBED_PATH_HINTS
    )


def _add_video_embed_candidate(video_embed_urls, seen, value):
    if value is None:
        return
    value = value.strip()
    if value == '':
        return
    if _is_video_embed_url(value) and value not in seen:
        seen.add(value)
        video_embed_urls.append(value)


def _collect_video_embeds_from_attrs(video_embed_urls, seen, tag_name, attrs):
    if tag_name not in ('iframe', 'embed'):
        return
    for name, value in attrs.items():
        if name.lower() in VIDEO_EMBED_ATTR_NAMES:
            _add_video_embed_candidate(video_embed_urls, seen, value)


def _read_text_response(response, max_bytes=None):
    content_length = response.headers.get('Content-Length')
    if max_bytes is not None and content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise RuntimeError('The source page is too large to process.')
        except ValueError:
            pass

    data = response.read(None if max_bytes is None else max_bytes + 1)
    if max_bytes is not None and len(data) > max_bytes:
        raise RuntimeError('The source page is too large to process.')
    charset = response.headers.get_content_charset() or 'utf-8'
    return data.decode(charset, errors='replace')


def _fetch_text(url, referer=None, safe_network=False):
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/126.0.0.0 Safari/537.36'
        )
    }
    if referer is not None:
        headers['Referer'] = referer
    request = urllib.request.Request(url, headers=headers)
    opener = open_public_url if safe_network else urllib.request.urlopen
    with opener(request, timeout=30) as response:
        return _read_text_response(
            response,
            max_bytes=MAX_SAFE_PAGE_BYTES if safe_network else None,
        )


def _pick_best_stream_variant(master_playlist):
    lines = [line.strip() for line in master_playlist.splitlines() if line.strip() != '']
    audio_uri = None
    variants = []
    for idx, line in enumerate(lines):
        if line.startswith('#EXT-X-MEDIA:TYPE=AUDIO') and audio_uri is None and 'URI="' in line:
            audio_uri = line.split('URI="', 1)[1].split('"', 1)[0]
        if not line.startswith('#EXT-X-STREAM-INF:'):
            continue
        next_idx = idx + 1
        while next_idx < len(lines) and lines[next_idx].startswith('#'):
            next_idx += 1
        if next_idx >= len(lines):
            continue
        bandwidth = 0
        resolution = 0
        for part in line.split(','):
            if 'BANDWIDTH=' in part:
                try:
                    bandwidth = int(part.split('BANDWIDTH=', 1)[1])
                except ValueError:
                    pass
            if 'RESOLUTION=' in part:
                value = part.split('RESOLUTION=', 1)[1]
                try:
                    width, height = value.split('x', 1)
                    resolution = int(width) * int(height)
                except ValueError:
                    pass
        variants.append((resolution, bandwidth, lines[next_idx]))
    if len(variants) == 0:
        return None, audio_uri
    variants.sort()
    return variants[-1][2], audio_uri


def _resolve_vimeo_stream_tasks(player_url, referer, index, safe_network=False):
    player_html = _fetch_text(
        player_url,
        referer=referer,
        safe_network=safe_network,
    )
    match = PLAYER_CONFIG_RE.search(player_html)
    if match is None:
        return [], None, None

    config = json.loads(match.group(1))
    files = config.get('request', {}).get('files', {})
    hls = files.get('hls')
    if hls is None:
        return [], config.get('video', {}).get('title'), None

    default_cdn = hls.get('default_cdn')
    cdns = hls.get('cdns', {})
    if default_cdn not in cdns or 'url' not in cdns[default_cdn]:
        return [], config.get('video', {}).get('title'), None

    master_url = cdns[default_cdn]['url']
    master_playlist = _fetch_text(master_url, safe_network=safe_network)
    video_uri, audio_uri = _pick_best_stream_variant(master_playlist)
    if video_uri is None:
        return [], config.get('video', {}).get('title'), None

    title = config.get('video', {}).get('title') or 'embedded-video-{}'.format(index)
    safe_title = safe_filename(title, fallback='embedded-video-{}'.format(index))
    mux_task = None
    tasks = [
        (
            urllib.parse.urljoin(master_url, video_uri),
            None,
            '{}-video.mp4'.format(safe_title),
        )
    ]
    if audio_uri is not None:
        tasks.append((
            urllib.parse.urljoin(master_url, audio_uri),
            None,
            '{}-audio.m4a'.format(safe_title),
        ))
        mux_task = {
            'video': '{}-video.mp4'.format(safe_title),
            'audio': '{}-audio.m4a'.format(safe_title),
            'output': '{}.mp4'.format(safe_title),
        }
    return tasks, title, mux_task


def _resolve_embedded_video_tasks(embed_url, page_url, index, safe_network=False):
    resolved_embed_url = complete_url(embed_url, page_url)

    if 'ypo.education' in resolved_embed_url:
        embed_html = _fetch_text(
            resolved_embed_url,
            referer=page_url,
            safe_network=safe_network,
        )
        iframe_match = IFRAME_SRC_RE.search(embed_html)
        if iframe_match is None:
            return [], resolved_embed_url, None
        player_url = complete_url(iframe_match.group(1), resolved_embed_url)
        tasks, title, mux_task = _resolve_vimeo_stream_tasks(
            player_url,
            referer=resolved_embed_url,
            index=index,
            safe_network=safe_network,
        )
        return tasks, title or resolved_embed_url, mux_task

    if 'player.vimeo.com' in resolved_embed_url:
        tasks, title, mux_task = _resolve_vimeo_stream_tasks(
            resolved_embed_url,
            referer=page_url,
            index=index,
            safe_network=safe_network,
        )
        return tasks, title or resolved_embed_url, mux_task

    return [], resolved_embed_url, None


class _GeneralMediaHTMLParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.media_urls = []
        self._seen = set()
        self.video_embed_urls = []
        self._video_embed_seen = set()
        self._title_parts = []
        self._in_title = False
        self._in_link = False

    @property
    def title(self):
        title = ''.join(self._title_parts).strip()
        return title if title else 'untitled'

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'title':
            self._in_title = True
        if tag == 'a':
            self._in_link = True
        _collect_media_from_attrs(self.media_urls, self._seen, attrs)
        _collect_video_embeds_from_attrs(self.video_embed_urls, self._video_embed_seen, tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        elif tag == 'a':
            self._in_link = False

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)
        if self._in_link:
            text = data.strip()
            _add_media_candidate(self.media_urls, self._seen, text)


def _parse_general_media(source):
    if bs is not None:
        media_urls = []
        seen = set()
        video_embed_urls = []
        video_embed_seen = set()
        soup = bs(source, 'html.parser')
        title_tag = soup.find('title')
        title = title_tag.text if title_tag is not None else 'untitled'
        for tag in soup.find_all(True):
            _collect_media_from_attrs(media_urls, seen, tag.attrs)
            _collect_video_embeds_from_attrs(video_embed_urls, video_embed_seen, tag.name, tag.attrs)
            _add_media_candidate(media_urls, seen, tag.get_text(strip=True))
        _collect_media_from_source(media_urls, seen, source)
        return title, media_urls, video_embed_urls

    parser = _GeneralMediaHTMLParser()
    parser.feed(source)
    _collect_media_from_source(parser.media_urls, parser._seen, source)
    return parser.title, parser.media_urls, parser.video_embed_urls

class Scraper(metaclass=ABCMeta):

    def __init__(
        self,
        driver='phantomjs',
        scroll_pause=1.0,
        next_page_pause=1.0,
        mode='normal',
        debug=False,
        safe_network=False,
    ):
        self._scroll_pause_time = scroll_pause
        self._next_page_pause_time = next_page_pause
        self._login_pause_time = 5.0
        self._mode = mode
        self._debug = debug
        self._name = 'scraper'
        self._driver = None
        self._current_url = None
        self._source = ''
        self._session = None
        self._safe_network = safe_network
        self._request_headers = {
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/126.0.0.0 Safari/537.36'
            )
        }

        if self._debug:
            driver = 'chrome'

        if driver == 'requests':
            if self._mode == 'verbose':
                print('Starting HTTP client...')
            if requests is not None:
                self._session = requests.Session()
                self._session.headers.update(self._request_headers)
        elif driver == 'phantomjs':
            if self._mode != 'silent':
                print('Starting PhantomJS web driver...')
            try:
                from util import seleniumdriver
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    'Selenium is not installed for this Python interpreter. '
                    'Use the default requests mode, or install the project requirements.'
                ) from exc
            self._driver = seleniumdriver.get('PhantomJS')
        elif driver == 'chrome':
            if self._mode == 'verbose':
                print('Starting Chrome web driver...')
            try:
                from util import seleniumdriver
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    'Selenium is not installed for this Python interpreter. '
                    'Use the default requests mode, or install the project requirements.'
                ) from exc
            self._driver = seleniumdriver.get('Chrome')
        elif driver == 'safari':
            if self._mode == 'verbose':
                print('Starting Safari web driver...')
            try:
                from util import seleniumdriver
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    'Selenium is not installed for this Python interpreter. '
                    'Use the default requests mode, or install the project requirements.'
                ) from exc
            self._driver = seleniumdriver.get('Safari')
        else:
            raise Exception('Driver not found "{}".'.format(driver))
            
        # self._driver.set_window_size(1920, 1080)

    def _connect(self, url):
        if self._debug:
            print('Connecting to "{}"...'.format(url))
        if self._driver is None:
            if self._session is not None and not self._safe_network:
                response = self._session.get(url, timeout=30)
                response.raise_for_status()
                self._current_url = response.url
                self._source = response.text
                return
            request = urllib.request.Request(url, headers=self._request_headers)
            opener = open_public_url if self._safe_network else urllib.request.urlopen
            with opener(request, timeout=30) as response:
                self._current_url = response.geturl()
                self._source = _read_text_response(
                    response,
                    max_bytes=MAX_SAFE_PAGE_BYTES if self._safe_network else None,
                )
            return
        self._driver.get(url)
        self._current_url = self._driver.current_url

    def source(self):
        if self._driver is None:
            return self._source
        return self._driver.page_source

    def current_url(self):
        if self._driver is None:
            return self._current_url
        return self._driver.current_url

    def print(self):
        print(self.source())

    def save(self, file):
        with open(file, 'wb') as f:
            f.write(self.source().encode('utf-8'))
        print('Saved web page to {}.'.format(file))

    def load_credentials(self, credentials_file):
        assert os.path.exists(credentials_file), 'Error: Credentials file "{}" does not exist.'.format(credentials_file)

        with open(credentials_file, 'r') as f:
            credentials = json.loads(f.read())

        assert self._name in credentials, 'Error: "{}" does not support credentials.'.format(self._name)

        credentials = credentials[self._name]
        if self._mode == 'verbose':
            user = credentials.keys().remove('password')
            print('Logging in as "{}"...'.format(credentials[user]))
        return credentials

    def find_element_by_class_name(self, class_name):
        if self._driver is None:
            return None
        try:
            element = self._driver.find_element_by_class_name(class_name)
            return element
        except:
            return None

    def scrollToBottom(self, fn=None, times=-1):
        if self._driver is None:
            return True
        if times < 0: times = sys.maxsize
        last_height, new_height = self._driver.execute_script("return document.body.scrollHeight"), 0
        counter = 0
        while (new_height != last_height or fn is not None and fn()) and counter < times:
            self._driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(self._scroll_pause_time)
            last_height = new_height
            new_height = self._driver.execute_script("return document.body.scrollHeight")
            counter += 1
        return not (new_height != last_height or fn is not None and fn())

    @abstractmethod
    def scrape(self):
        return None

    def download(self, tasks, path='.', force=False):
        if self._mode != 'silent':
            print('Downloading...')
        downloaded = []
        for url, folder, rename in tqdm(tasks):
            target_path = path
            if folder is not None:
                target_path = os.path.join(target_path, folder)
            downloaded_file = download(url, path=target_path, rename=rename, replace=force)
            if downloaded_file is not None:
                downloaded.append(downloaded_file)
        return downloaded

    @abstractmethod
    def login(self):
        pass

class MediaScraper(Scraper):

    def __init__(self, **kwargs):
        kwargs.setdefault('driver', 'requests')
        super().__init__(**kwargs)
        self._name = 'general'
        self._embedded_video_urls = []
        self._embedded_video_mux_tasks = []
        self._page_title = 'untitled'
        # self.abs_url_regex = '([a-z0-9]*:|.{0})\/\/[^"\s]+'
        # self.rel_url_regex = '\"[^\/]+\/[^\/].*$|^\/[^\/].*\"'
        # self.abs_url_regex = '/^([a-z0-9]*:|.{0})\/\/.*$/gmi'
        # self.rel_url_regex = '/^[^\/]+\/[^\/].*$|^\/[^\/].*$/gmi'

    def scrape(self, url):
        self._connect(url)
        self.scrollToBottom()

        if self._debug:
            self.save('test.html')

        source = self.source()

        # Parse links, images, and videos successively by BeautifulSoup parser.

        title, media_urls, video_embed_urls = _parse_general_media(source)
        self._page_title = title.strip() or 'untitled'
        self._embedded_video_urls = [
            complete_url(video_embed_url, self.current_url())
            for video_embed_url in video_embed_urls
        ]
        self._embedded_video_mux_tasks = []
        embedded_video_tasks = []
        resolved_embedded_video_titles = []
        for idx, video_embed_url in enumerate(self._embedded_video_urls, start=1):
            try:
                resolved_tasks, resolved_title, mux_task = _resolve_embedded_video_tasks(
                    video_embed_url,
                    self.current_url(),
                    idx,
                    safe_network=self._safe_network,
                )
                embedded_video_tasks += resolved_tasks
                if len(resolved_tasks) > 0:
                    resolved_embedded_video_titles.append(resolved_title)
                if mux_task is not None:
                    mux_task['folder'] = title
                    self._embedded_video_mux_tasks.append(mux_task)
            except Exception as exc:
                if self._mode != 'silent':
                    print('Failed to resolve embedded video "{}": {}'.format(video_embed_url, exc))

        if self._debug:
            print(media_urls)

        tasks = [(complete_url(media_url, self.current_url()), title, None) for media_url in media_urls]
        tasks += [(url, title, rename) for url, _, rename in embedded_video_tasks]

        if self._debug:
            print(tasks)

        if self._mode != 'silent':
            print('{} media are found.'.format(len(media_urls)))
            if len(self._embedded_video_urls) > 0:
                print('{} embedded video page(s) are found:'.format(len(self._embedded_video_urls)))
                for video_embed_url in self._embedded_video_urls:
                    print(video_embed_url)
            if len(embedded_video_tasks) > 0:
                print('{} embedded video stream file(s) are resolved:'.format(len(embedded_video_tasks)))
                for resolved_title in resolved_embedded_video_titles:
                    print(resolved_title)

        return tasks


        # # Parse links, images, and videos successively by native regex matching.

        # urls = re.findall('http', source)
        # print('test urls:')
        # for url in urls:
        #     print(url)

        # urls = re.findall(self.abs_url_regex, source)
        # print('abs urls:')
        # for url in urls:
        #     print(url)

        # urls = re.findall(self.rel_url_regex, source)
        # print('rel urls:')
        # for url in urls:
        #     print(url)

    def login(self, credentials_file):
        pass

    def embedded_video_urls(self):
        return list(self._embedded_video_urls)

    def embedded_video_mux_tasks(self):
        return [dict(task) for task in self._embedded_video_mux_tasks]

    def page_title(self):
        return self._page_title

    def scrape_videos(self, url):
        tasks = self.scrape(url)
        component_names = set()
        for mux_task in self._embedded_video_mux_tasks:
            component_names.add(mux_task['video'])
            component_names.add(mux_task['audio'])
            mux_task['folder'] = None

        video_tasks = []
        for media_url, _, rename in tasks:
            filename = rename or get_filename(media_url)
            if filename in component_names or is_video(media_url) or is_video(filename):
                video_tasks.append((media_url, None, rename))
        return video_tasks

    def download(self, tasks, path='.', force=False):
        downloaded = super().download(tasks, path=path, force=force)
        for mux_task in self._embedded_video_mux_tasks:
            target_path = path
            if mux_task['folder'] is not None:
                target_path = os.path.join(target_path, mux_task['folder'])
            output_path = os.path.join(target_path, mux_task['output'])
            merged = mux_audio_video(
                os.path.join(target_path, mux_task['video']),
                os.path.join(target_path, mux_task['audio']),
                output_path,
                replace=force,
            )
            if merged or os.path.exists(output_path):
                downloaded.append(output_path)
        return downloaded


class InstagramScraper(Scraper):

    # NOTES: 
    # 1. Naming rule of Instagram username: 
    #    (1) letters    (a-zA-Z) 
    #    (2) digits     (0-9) 
    #    (3) underline  (_) 
    #    (4) dot        (.) 
    # 2. Shortcode: 
    #    not necessarily is a string of 11 characters 
    #    maybe a string of 38 (on private account)
    # 3. In a page, there are at most 30 rows of posts.
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._name = 'instagram'

        self.login_url = 'https://www.instagram.com/accounts/login/'
        self.json_data_url = 'https://www.instagram.com/{}/?__a=1'
        self.json_data_url_with_max_id = 'https://www.instagram.com/{}/?__a=1&max_id={}'
        self.new_json_data_url = 'https://www.instagram.com/graphql/query/?query_hash={}&variables={{"id":"{}","first":{},"after":"{}"}}'
        self.query_parameters = {
            'query_hash': '472f257a40c653c64c666ce877d59d2b', 
            'first': 12
        }
        self.post_regex = r'/p/[^/]+/'

    # def getJsonData(self, target, max_id=None):
    #     if max_id is None:
    #         self._connect(self.json_data_url.format(target))
    #     else:
    #         self._connect(self.json_data_url_with_max_id.format(target, max_id))
    #     content = self._driver.find_element_by_tag_name('pre').text
    #     data = json.loads(content)
    #     return data

    def getJsonData(self, user_or_id, after=None):
        from util.instagram import parse_node
        if after is None:   # user_or_id should be username
            self._connect(self.json_data_url.format(user_or_id))
        else:               # user_or_id should be id
            self._connect(self.new_json_data_url.format(
                self.query_parameters['query_hash'], user_or_id, self.query_parameters['first'], after))
        content = self._driver.find_element_by_tag_name('pre').text
        data = json.loads(content)
        return data

    def sharedData(self):
        return self._driver.execute_script("return window._sharedData")

    def scrape(self, username):
        from util.instagram import parse_node
        if self._mode != 'silent':
            print('Crawling...')

        data = self.getJsonData(username)

        # user = data['user']
        # media = user['media']
        # nodes = media['nodes']

        user = data['graphql']['user']
        user_id = user['id']
        media = user['edge_owner_to_timeline_media']
        count = media['count']
        # print('Count: {}'.format(count))
        page_info = media['page_info']
        edges = media['edges']
        has_next_page = page_info['has_next_page']
        end_cursor = page_info['end_cursor']

        tasks = []
        num_post = 0
        while len(edges) > 0:
            num_post += len(edges)
            for edge in edges:
                # post = self.getJsonData('p/'+node['code'])
                post = self.getJsonData('p/'+edge['node']['shortcode'])
                task = parse_node(post['graphql']['shortcode_media'])
                tasks += (task[0], username, task[1])
            # nodes = data['user']['media']['nodes']
            if has_next_page:
                # data = self.getJsonData(username, edges[-1]['node']['id'])
                data = self.getJsonData(user_id, end_cursor)
                try:
                    edges = data['data']['user']['edge_owner_to_timeline_media']['edges']
                except Exception as e:
                    print(data)
                    print(e)
                has_next_page = data['data']['user']['edge_owner_to_timeline_media']['page_info']['has_next_page']
                end_cursor = data['data']['user']['edge_owner_to_timeline_media']['page_info']['end_cursor']
            else:
                break

        if self._mode != 'silent':
            print('{} posts are found.'.format(num_post))

        if self._mode != 'silent':
            print('{} media are found.'.format(len(tasks)))

        return tasks

    def scrapePage(self, username):
        from util.instagram import parse_node
        self._connect('{}/{}/'.format(self.base_url, username))

        if self._mode != 'silent':
            print('Crawling...')
        done = False
        codes = re.findall(self.post_regex, self.source())
        while not done:
            done = self.scrollToBottom(fn=lambda: self.find_element_by_class_name('_o5uzb'), times=2)
            codes += re.findall(self.post_regex, self.source())
        codes = list(set(codes))
        codes = [code[3:-1] for code in codes]

        if self._mode != 'silent':
            print('{} posts are found.'.format(len(codes)))

        if self._debug:
            self.save('test.html')
            with open('shortcodes.txt', 'w') as f:
                f.write(json.dumps(codes))

        if self._mode != 'silent':
            print('Scraping...')

        tasks = []
        for code in tqdm(codes):
            self._connect('{}/p/{}/'.format(self.base_url, code))
            data = self.sharedData()
            node = data['entry_data']['PostPage'][0]['graphql']['shortcode_media']
            tasks += parse_node(node, node['owner']['username'])

        if self._mode != 'silent':
            print('{} media are found.'.format(len(tasks)))

        return tasks

    def scrapeSharedData(self):
        sharedData = self.sharedData()
        profilePage = sharedData['entry_data']['ProfilePage']
        print('# of profilePage: {}.'.format(len(profilePage)))
        user = profilePage[0]['user']
        print('# of following: {}.'.format(user['follows']['count']))
        print('Url of profile picture: {}.'.format(user['profile_pic_url_hd']))
        print('Full name: {}.'.format(user['full_name']))
        print('# of followers: {}.'.format(user['followed_by']))
        # user['media_collections']
        media = user['media']
        print('# of media: {}.'.format(media['count']))
        nodes = media['nodes']
        target = []
        for node in nodes:
            # node['date']
            # node['comments']['count']
            # node['is_video']
            # node['id']
            # node['__typename']
            target.append(node['code'])
            # node['likes']['count']
            # node['caption']
        # user['is_private']
        # user['username']

        with open('json.txt', 'w') as f:
            f.write(json.dumps(sharedData))

        with open('ids_shared_data.txt', 'w') as f:
            f.write(json.dumps(target))

    def login(self, credentials_file):
        credentials = self.load_credentials(credentials_file)
        return
        
        if credentials['username'] == '' or credentials['password'] == '':
            print('Either username or password is empty. Abort login.')

        if self._mode != 'silent':
            print('Logging in as "{}"...'.format(credentials['username']))

        self._connect(self.login_url)
        time.sleep(self._login_pause_time)

        username, password = self._driver.find_elements_by_tag_name('input')
        button = self._driver.find_element_by_tag_name('button')

        username.send_keys(credentials['username'])
        password.send_keys(credentials['password'])
        button.click()
        time.sleep(self._login_pause_time)


class TwitterScraper(Scraper):
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._name = 'twitter'

        self.base_url = 'https://twitter.com'
        self.login_url = 'https://twitter.com/login'
        # self.post_regex = '/p/[ -~]{11}/'
        self.scroll_pause = 3.0

    def scrape(self, username):
        from util.twitter import get_twitter_video_url
        self._connect('{}/{}/media'.format(self.base_url, username))

        if self._mode != 'silent':
            print('Crawling...')

        done = self.scrollToBottom()

        source = self.source()
        soup = bs(source, 'html.parser')

        # title = soup.find('title')
        # name = title.get_text().replace('Media Tweets by ', '').replace(' | Twitter', '')

        # avatar_url = soup.find("a", { "class" : "ProfileCardMini-avatar" }).get('data-resolved-url-large')
        # background_url = soup.find("div", { "class" : "ProfileCanopy-headerBg" }).find('img').get('src')

        tasks = []
        for li in soup.find_all('li', {'class': 'js-stream-item stream-item stream-item '}):
            photos = li.find_all('div', { "class" : "AdaptiveMedia-photoContainer" })
            if photos == []:
                try:
                    img_url, vid_url = get_twitter_video_url(li['data-item-id'])
                    tasks.append((img_url+':large', username, get_basename(get_filename(img_url))))
                    tasks.append((vid_url, username, get_basename(get_filename(vid_url))))
                except Exception as e:
                    with open('error.txt', 'w', encoding='utf-8') as f:
                        f.write(str(e) + '\n')
                        f.write(str(li))
            else:
                for photo in photos:
                    url = photo['data-image-url']
                    tasks.append((url+':large', username, get_basename(get_filename(url))))
        for div in soup.find_all('div', { "class" : "AdaptiveMedia-photoContainer" }):
            url = div['data-image-url']
            tasks.append((url+':large', username, get_basename(get_filename(url))))

        if self._mode != 'silent':
            print('{} media are found.'.format(len(tasks)))

        return tasks

    def login(self, credentials_file):
        credentials = self.load_credentials(credentials_file)
        
        if credentials['username'] == '' or credentials['password'] == '':
            print('Either username or password is empty. Abort login.')
            return

        if self._mode != 'silent':
            print('Logging in as "{}"...'.format(credentials['username']))

        self._connect(self.login_url)
        time.sleep(self._login_pause_time)

        usernames = self._driver.find_elements_by_name('session[username_or_email]')
        passwords = self._driver.find_elements_by_name('session[password]')
        buttons = self._driver.find_elements_by_tag_name('button')
        username = [u for u in usernames if u.get_attribute('class') == 'js-username-field email-input js-initial-focus'][0]
        password = [p for p in passwords if p.get_attribute('class') == 'js-password-field'][0]
        button = [b for b in buttons if b.text != ''][0]
        self._driver.save_screenshot('test.png')
        self._driver.implicitly_wait(10)

        username.send_keys(credentials['username'])
        password.send_keys(credentials['password'])
        button.click()
        time.sleep(self._login_pause_time)


class FacebookScraper(Scraper):
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._name = 'facebook'

        self.base_url = 'https://www.facebook.com'
        self.login_url = 'https://www.facebook.com/login'
        # self.post_regex = '/p/[ -~]{11}/'

    def scrape(self, username):
        self._connect('{}/{}/media'.format(self.base_url, username))

        if self._mode != 'silent':
            print('Crawling...')

        done = self.scrollToBottom()

        source = self.source()
        soup = bs(source, 'html.parser')

        # title = soup.find('title')
        # name = title.get_text().replace('Media Tweets by ', '').replace(' | Twitter', '')

        # avatar_url = soup.find("a", { "class" : "ProfileCardMini-avatar" }).get('data-resolved-url-large')
        # background_url = soup.find("div", { "class" : "ProfileCanopy-headerBg" }).find('img').get('src')

        tasks = []
        for div in soup.find_all('div', { "class" : "AdaptiveMedia-photoContainer" }):
            url = div.get('data-image-url')
            tasks.append((url+':large', username, get_filename(url)))

        if self._mode != 'silent':
            print('{} media are found.'.format(len(tasks)))

        return tasks

    def login(self, credentials_file):
        credentials = self.load_credentials(credentials_file)
        
        if credentials['email'] == '' or credentials['password'] == '':
            print('Either email or password is empty. Abort login.')
            return

        if self._mode != 'silent':
            print('Logging in as "{}"...'.format(credentials['email']))

        self._connect(self.login_url)
        time.sleep(self._login_pause_time)

        email = self._driver.find_element_by_tag_name('email')
        password = self._driver.find_element_by_tag_name('pass')
        buttons = self._driver.find_element_by_tag_name('login')

        username.send_keys(credentials['email'])
        password.send_keys(credentials['password'])
        button.click()
        time.sleep(self._login_pause_time)


class pixivScraper(Scraper):
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._name = 'pixiv'

        self.base_url = 'https://www.pixiv.net'
        self.post_url = 'https://www.pixiv.net/member_illust.php?id='
        self.login_url = 'https://accounts.pixiv.net/login'
        # self.post_regex = '/p/[ -~]{11}/'

    def scrape(self, id, content_type='all'):
        self._connect(self.post_url + id)
        self.id = id
        self.type = content_type

        if self._mode != 'silent':
            print('Crawling...')

        # TODO

        # get page num
        pager_container = self._driver.get_element_by_class_name('page-list')
        last_pager = pager_container.get_element_by_tag_name('li')[-1]
        num_page = int(last_pager.get_element_by_tag_name('a').text)
        print('# of page: {}'.format(num_page))

        # crawl each page
        for p in range(1, num_page+1):
            url = 'https://www.pixiv.net/member_illust.php?id={}&type={}&p={}'.format(self.id, self.type, p)
            self._driver._connect(url)
            time.sleep(self._next_page_pause_time)
            print(url)
        # scrape each post

        return

        done = self.scrollToBottom()

        source = self.source()
        soup = bs(source, 'html.parser')

        # title = soup.find('title')
        # name = title.get_text().replace('Media Tweets by ', '').replace(' | Twitter', '')

        # avatar_url = soup.find("a", { "class" : "ProfileCardMini-avatar" }).get('data-resolved-url-large')
        # background_url = soup.find("div", { "class" : "ProfileCanopy-headerBg" }).find('img').get('src')

        tasks = []
        for div in soup.find_all('div', { "class" : "AdaptiveMedia-photoContainer" }):
            url = div.get('data-image-url')
            tasks.append((url+':large', get_filename(url)))

        if self._mode != 'silent':
            print('{} media are found.'.format(len(tasks)))

        return tasks

    def login(self, credentials_file):
        credentials = self.load_credentials(credentials_file)
        
        if credentials['username'] == '' or credentials['password'] == '':
            print('Either username or password is empty. Abort login.')
            return

        if self._mode != 'silent':
            print('Logging in as "{}"...'.format(credentials['username']))

        self._connect(self.login_url)
        time.sleep(self._login_pause_time)

        container = self._driver.find_element_by_id('container-login')

        username, password = container.find_elements_by_tag_name('input')
        buttons = container.find_element_by_tag_name('button')

        username.send_keys(credentials['username'])
        password.send_keys(credentials['password'])
        button.click()
        time.sleep(self._login_pause_time)
