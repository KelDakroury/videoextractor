#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2018 Elvis Yu-Jing Lin <elvisyjlin@gmail.com>
# Licensed under the MIT License - https://opensource.org/licenses/MIT

import os
import urllib
import urllib.error
import urllib.parse
import urllib.request
from util.file import rename_file, safe_filename, safe_makedirs
from util.network import open_public_url

REQUEST_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/126.0.0.0 Safari/537.36'
    )
}
MAX_HLS_SEGMENTS = 3000


class DownloadTooLargeError(RuntimeError):
    pass


def get_filename(url):
    return url.rsplit('/', 1)[-1].split(':')[0].split('?', 1)[0]

def complete_url(url, current_url):
    if ':' not in url:
        if url.startswith('//'):
            url = 'http:' + url
        elif url.startswith('/'):
            base_url = '/'.join(current_url.split('/')[:3])
            url = base_url + url
        else:
            dir_url = current_url.rsplit('/', 1)[0]
            url = dir_url + '/' + url
    return url

def _open_url(url, headers=None, timeout=30, safe_network=False):
    request_headers = dict(REQUEST_HEADERS)
    if headers is not None:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    if safe_network:
        return open_public_url(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def _copy_response(response, output, written=0, max_bytes=None):
    content_length = response.headers.get('Content-Length')
    if max_bytes is not None and content_length is not None:
        try:
            if written + int(content_length) > max_bytes:
                raise DownloadTooLargeError(
                    'The video is larger than the {} MB download limit.'.format(
                        max_bytes // (1024 * 1024)
                    )
                )
        except ValueError:
            pass

    while True:
        chunk = response.read(1024 * 256)
        if not chunk:
            break
        written += len(chunk)
        if max_bytes is not None and written > max_bytes:
            raise DownloadTooLargeError(
                'The video is larger than the {} MB download limit.'.format(
                    max_bytes // (1024 * 1024)
                )
            )
        output.write(chunk)
    return written


def _download_hls_media_playlist(url, file, max_bytes=None, safe_network=False):
    with _open_url(url, safe_network=safe_network) as response:
        playlist = response.read().decode('utf-8', errors='replace')

    lines = [line.strip() for line in playlist.splitlines() if line.strip() != '']
    if any(
        line.startswith('#EXT-X-KEY:') and 'METHOD=NONE' not in line
        for line in lines
    ):
        raise RuntimeError('Encrypted HLS video is not supported.')
    if any(line.startswith('#EXT-X-BYTERANGE:') for line in lines):
        raise RuntimeError('Byte-range HLS video is not supported.')
    segment_count = sum(not line.startswith('#') for line in lines)
    if segment_count > MAX_HLS_SEGMENTS:
        raise RuntimeError('The HLS playlist contains too many media segments.')

    safe_makedirs(os.path.dirname(file) or '.')
    written = 0
    with open(file, 'wb') as output:
        for line in lines:
            if line.startswith('#EXT-X-MAP:'):
                uri = line.split('URI="', 1)[1].split('"', 1)[0]
                init_url = urllib.parse.urljoin(url, uri)
                with _open_url(init_url, safe_network=safe_network) as init_response:
                    written = _copy_response(init_response, output, written, max_bytes)
            elif not line.startswith('#'):
                segment_url = urllib.parse.urljoin(url, line)
                with _open_url(segment_url, safe_network=safe_network) as segment_response:
                    written = _copy_response(segment_response, output, written, max_bytes)
    return file

def _pick_best_hls_variant(lines):
    variants = []
    for idx, line in enumerate(lines):
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
        return None
    variants.sort()
    return variants[-1][2]

def download_hls(url, file, max_bytes=None, safe_network=False):
    with _open_url(url, safe_network=safe_network) as response:
        playlist = response.read().decode('utf-8', errors='replace')

    lines = [line.strip() for line in playlist.splitlines() if line.strip() != '']
    if any(line.startswith('#EXT-X-STREAM-INF:') for line in lines):
        variant = _pick_best_hls_variant(lines)
        if variant is None:
            raise RuntimeError('No playable HLS variants found.')
        return download_hls(
            urllib.parse.urljoin(url, variant),
            file,
            max_bytes=max_bytes,
            safe_network=safe_network,
        )

    return _download_hls_media_playlist(
        url,
        file,
        max_bytes=max_bytes,
        safe_network=safe_network,
    )


def download(
    url,
    path='.',
    rename=None,
    replace=True,
    headers=None,
    max_bytes=None,
    safe_network=False,
    raise_errors=False,
):
    if '.m3u8' in url:
        default_name = urllib.parse.unquote(get_filename(url))
        if rename is None:
            base_name = default_name.rsplit('.', 1)[0] or 'video'
            filename = '{}.mp4'.format(base_name)
        elif os.path.splitext(rename)[1] != '':
            filename = rename
        else:
            filename = '{}.mp4'.format(rename)
    elif rename is None:
        filename = urllib.parse.unquote(get_filename(url))
    elif os.path.splitext(rename)[1] != '':
        filename = rename
    else:
        filename = rename_file(get_filename(url), rename)

    filename = safe_filename(filename, fallback='video.mp4')
    file = os.path.join(path, filename)

    if not replace and os.path.exists(file):
        print('The file {} exists. Skip it.'.format(file))
        return file

    safe_makedirs(path)
    try:
        if '.m3u8' in url:
            download_hls(
                url,
                file,
                max_bytes=max_bytes,
                safe_network=safe_network,
            )
        else:
            with _open_url(
                url,
                headers=headers,
                safe_network=safe_network,
            ) as response:
                with open(file, 'wb') as f:
                    _copy_response(response, f, max_bytes=max_bytes)
    except urllib.error.HTTPError as exc:
        if os.path.exists(file):
            os.remove(file)
        if raise_errors:
            raise
        print('Error: status code of {} "{}" is {}.'.format(filename, url, exc.code))
        return None
    except Exception:
        if os.path.exists(file):
            os.remove(file)
        raise

    return file

# Guess the type of url by its mime format.
#
# IANA - MIME
# Media Types: http://www.iana.org/assignments/media-types/media-types.xhtml

import mimetypes

def get_mimetype(url):
    return mimetypes.guess_type(url, strict=False)[0]

def is_image(url):
    mimetype = get_mimetype(url)
    return None if mimetype is None else mimetype.split('/')[0] == 'image'

def is_video(url):
    if '.m3u8' in url.lower():
        return True
    mimetype = get_mimetype(url)
    return None if mimetype is None else mimetype.split('/')[0] == 'video'

def is_media(url):
    return bool(is_image(url) or is_video(url))
