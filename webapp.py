#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import mimetypes
import os
import re
import shutil
import threading
import time
import traceback
import urllib.error
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from util.network import UnsafeURLError, validate_public_url
from util.url import DownloadTooLargeError
from video_service import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    DEFAULT_MAX_VIDEO_HEIGHT,
    VideoNotFoundError,
    VideoProcessingError,
    extract_videos,
)


PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PROJECT_ROOT / 'web' / 'static'
DEFAULT_JOB_ROOT = PROJECT_ROOT / 'download' / 'web'
MAX_REQUEST_BYTES = 16 * 1024
JOB_ID_RE = r'([0-9a-f]{32})'
JOB_RE = re.compile(r'^/api/jobs/{}/?$'.format(JOB_ID_RE))
FILE_RE = re.compile(r'^/api/jobs/{}/files/(\d+)/?$'.format(JOB_ID_RE))


class JobCapacityError(RuntimeError):
    pass


class JobStore:

    def __init__(self, root=DEFAULT_JOB_ROOT, ttl_seconds=3600, max_active_jobs=10):
        self.root = Path(root).resolve()
        self.ttl_seconds = ttl_seconds
        self.max_active_jobs = max_active_jobs
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs = {}
        self._lock = threading.RLock()
        self._clean_orphan_directories()

    def _clean_orphan_directories(self):
        cutoff = time.time() - self.ttl_seconds
        for path in self.root.iterdir():
            try:
                if path.is_dir() and path.stat().st_mtime < cutoff:
                    shutil.rmtree(path)
            except OSError:
                pass

    def cleanup(self):
        cutoff = time.time() - self.ttl_seconds
        expired = []
        with self._lock:
            for job_id, job in self._jobs.items():
                if job['status'] in ('ready', 'failed') and job['updated_at'] < cutoff:
                    expired.append((job_id, job['directory']))
            for job_id, _ in expired:
                self._jobs.pop(job_id, None)

        for _, directory in expired:
            shutil.rmtree(directory, ignore_errors=True)

    def create(self, source_url):
        self.cleanup()
        with self._lock:
            active_jobs = sum(
                job['status'] in ('queued', 'extracting', 'downloading', 'processing')
                for job in self._jobs.values()
            )
            if active_jobs >= self.max_active_jobs:
                raise JobCapacityError(
                    'The service is busy right now. Please try again in a moment.'
                )

            job_id = uuid.uuid4().hex
            directory = self.root / job_id
            directory.mkdir(parents=True, exist_ok=False)
            now = time.time()
            self._jobs[job_id] = {
                'id': job_id,
                'source_url': source_url,
                'status': 'queued',
                'stage': 'queued',
                'progress': 4,
                'message': 'Waiting for a download worker',
                'title': None,
                'files': [],
                'error': None,
                'directory': str(directory),
                'created_at': now,
                'updated_at': now,
            }
            return self.public(job_id)

    def get_internal(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else dict(job)

    def update(self, job_id, **changes):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.update(changes)
            job['updated_at'] = time.time()

    def complete(self, job_id, title, files):
        self.update(
            job_id,
            status='ready',
            stage='ready',
            progress=100,
            message='Your video is ready',
            title=title,
            files=files,
            error=None,
        )

    def fail(self, job_id, message):
        self.update(
            job_id,
            status='failed',
            stage='failed',
            message='The video could not be prepared',
            error=message,
        )

    def public(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            files = [
                {
                    'name': file['name'],
                    'size': file['size'],
                    'play_url': '/api/jobs/{}/files/{}'.format(job_id, index),
                    'download_url': '/api/jobs/{}/files/{}?download=1'.format(
                        job_id,
                        index,
                    ),
                }
                for index, file in enumerate(job['files'])
            ]
            return {
                'id': job['id'],
                'source_url': job['source_url'],
                'status': job['status'],
                'stage': job['stage'],
                'progress': job['progress'],
                'message': job['message'],
                'title': job['title'],
                'files': files,
                'error': job['error'],
                'expires_in': max(
                    0,
                    int(self.ttl_seconds - (time.time() - job['updated_at'])),
                ),
            }

    def file(self, job_id, index):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job['status'] != 'ready':
                return None
            if index < 0 or index >= len(job['files']):
                return None
            return dict(job['files'][index])


def _friendly_error(exc):
    if isinstance(
        exc,
        (
            UnsafeURLError,
            VideoNotFoundError,
            VideoProcessingError,
            DownloadTooLargeError,
        ),
    ):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        return 'The source website returned HTTP {}.'.format(exc.code)
    if isinstance(exc, urllib.error.URLError):
        return 'The source website could not be reached.'
    if isinstance(exc, TimeoutError):
        return 'The source website took too long to respond.'
    return 'An unexpected processing error occurred. Check the server log for details.'


def process_job(
    store,
    job_id,
    max_download_bytes=DEFAULT_MAX_DOWNLOAD_BYTES,
    max_video_height=DEFAULT_MAX_VIDEO_HEIGHT,
):
    job = store.get_internal(job_id)
    if job is None:
        return

    store.update(
        job_id,
        status='extracting',
        stage='extracting',
        progress=10,
        message='Inspecting the page for video',
    )

    def report(stage, progress, message):
        store.update(
            job_id,
            status=stage,
            stage=stage,
            progress=progress,
            message=message,
        )

    try:
        result = extract_videos(
            job['source_url'],
            job['directory'],
            progress_callback=report,
            max_download_bytes=max_download_bytes,
            max_video_height=max_video_height,
        )
        store.complete(job_id, result['title'], result['files'])
    except Exception as exc:
        if not isinstance(
            exc,
            (
                UnsafeURLError,
                VideoNotFoundError,
                VideoProcessingError,
                DownloadTooLargeError,
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
            ),
        ):
            traceback.print_exc()
        store.fail(job_id, _friendly_error(exc))


class MediaScraperHandler(BaseHTTPRequestHandler):
    server_version = 'MediaScraperWeb/1.0'

    def do_GET(self):
        self._route(send_body=True)

    def do_HEAD(self):
        self._route(send_body=False)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path != '/api/jobs':
            self._send_json(404, {'error': 'Not found.'})
            return

        content_length = self.headers.get('Content-Length')
        try:
            body_length = int(content_length or '0')
        except ValueError:
            self._send_json(400, {'error': 'Invalid request length.'})
            return

        if body_length <= 0 or body_length > MAX_REQUEST_BYTES:
            self._send_json(413, {'error': 'The request is too large.'})
            return

        try:
            payload = json.loads(self.rfile.read(body_length).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {'error': 'Send a JSON object containing a URL.'})
            return

        source_url = payload.get('url') if isinstance(payload, dict) else None
        try:
            source_url = validate_public_url(source_url)
            job = self.server.job_store.create(source_url)
        except (UnsafeURLError, JobCapacityError) as exc:
            status = 429 if isinstance(exc, JobCapacityError) else 400
            self._send_json(status, {'error': str(exc)})
            return

        self.server.executor.submit(
            process_job,
            self.server.job_store,
            job['id'],
            self.server.max_download_bytes,
            self.server.max_video_height,
        )
        self._send_json(202, job)

    def _route(self, send_body):
        self.server.job_store.cleanup()
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path

        if path == '/':
            self._send_static('index.html', 'text/html; charset=utf-8', send_body)
            return
        if path == '/assets/styles.css':
            self._send_static('styles.css', 'text/css; charset=utf-8', send_body)
            return
        if path == '/assets/app.js':
            self._send_static('app.js', 'text/javascript; charset=utf-8', send_body)
            return
        if path == '/api/health':
            self._send_json(200, {'status': 'ok'}, send_body=send_body)
            return

        job_match = JOB_RE.match(path)
        if job_match is not None:
            job = self.server.job_store.public(job_match.group(1))
            if job is None:
                self._send_json(404, {'error': 'This job was not found or has expired.'})
                return
            self._send_json(200, job, send_body=send_body)
            return

        file_match = FILE_RE.match(path)
        if file_match is not None:
            job_id, index = file_match.groups()
            file = self.server.job_store.file(job_id, int(index))
            if file is None:
                self._send_json(404, {'error': 'This video was not found or has expired.'})
                return
            download = urllib.parse.parse_qs(parsed.query).get('download') == ['1']
            self._send_file(file, download=download, send_body=send_body)
            return

        self._send_json(404, {'error': 'Not found.'}, send_body=send_body)

    def _send_static(self, filename, content_type, send_body):
        file_path = STATIC_ROOT / filename
        if not file_path.is_file():
            self._send_json(404, {'error': 'Static asset not found.'})
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self._security_headers()
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _send_json(self, status, payload, send_body=True):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self._security_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _send_file(self, file, download, send_body):
        path = Path(file['path'])
        if not path.is_file():
            self._send_json(404, {'error': 'The video file is no longer available.'})
            return

        size = path.stat().st_size
        start = 0
        end = max(0, size - 1)
        status = 200
        range_header = self.headers.get('Range')
        if range_header:
            parsed_range = self._parse_range(range_header, size)
            if parsed_range is None:
                self.send_response(416)
                self._security_headers()
                self.send_header('Content-Range', 'bytes */{}'.format(size))
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            start, end = parsed_range
            status = 206

        length = 0 if size == 0 else end - start + 1
        content_type = mimetypes.guess_type(file['name'])[0] or 'application/octet-stream'
        disposition = 'attachment' if download else 'inline'
        ascii_name = file['name'].encode('ascii', errors='replace').decode('ascii')
        encoded_name = urllib.parse.quote(file['name'])

        self.send_response(status)
        self._security_headers()
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header(
            'Content-Disposition',
            '{}; filename="{}"; filename*=UTF-8\'\'{}'.format(
                disposition,
                ascii_name.replace('"', ''),
                encoded_name,
            ),
        )
        self.send_header('Cache-Control', 'private, max-age=3600')
        if status == 206:
            self.send_header(
                'Content-Range',
                'bytes {}-{}/{}'.format(start, end, size),
            )
        self.end_headers()

        if not send_body or length == 0:
            return

        try:
            with path.open('rb') as stream:
                stream.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = stream.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def _parse_range(value, size):
        if size <= 0 or not value.startswith('bytes=') or ',' in value:
            return None
        spec = value[6:].strip()
        if '-' not in spec:
            return None
        start_text, end_text = spec.split('-', 1)
        try:
            if start_text == '':
                suffix_length = int(end_text)
                if suffix_length <= 0:
                    return None
                start = max(0, size - suffix_length)
                end = size - 1
            else:
                start = int(start_text)
                end = size - 1 if end_text == '' else int(end_text)
        except ValueError:
            return None
        if start < 0 or start >= size or end < start:
            return None
        return start, min(end, size - 1)

    def _security_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "media-src 'self' blob:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "form-action 'self'",
        )

    def log_message(self, format, *args):
        print(
            '{} - - [{}] {}'.format(
                self.client_address[0],
                self.log_date_time_string(),
                format % args,
            )
        )


def create_server(
    host='127.0.0.1',
    port=8000,
    job_root=DEFAULT_JOB_ROOT,
    ttl_seconds=3600,
    max_active_jobs=10,
    workers=2,
    max_download_bytes=DEFAULT_MAX_DOWNLOAD_BYTES,
    max_video_height=DEFAULT_MAX_VIDEO_HEIGHT,
):
    server = ThreadingHTTPServer((host, port), MediaScraperHandler)
    server.daemon_threads = True
    server.job_store = JobStore(
        root=job_root,
        ttl_seconds=ttl_seconds,
        max_active_jobs=max_active_jobs,
    )
    server.executor = ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix='media-scraper',
    )
    server.max_download_bytes = max_download_bytes
    server.max_video_height = max_video_height
    return server


def main():
    host = os.environ.get('MEDIA_SCRAPER_HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '8000'))
    ttl_seconds = int(os.environ.get('MEDIA_SCRAPER_JOB_TTL', '3600'))
    workers = int(os.environ.get('MEDIA_SCRAPER_WORKERS', '2'))
    max_active_jobs = int(os.environ.get('MEDIA_SCRAPER_MAX_JOBS', '10'))
    max_download_mb = int(os.environ.get('MEDIA_SCRAPER_MAX_MB', '500'))
    max_video_height = int(
        os.environ.get(
            'MEDIA_SCRAPER_MAX_HEIGHT',
            str(DEFAULT_MAX_VIDEO_HEIGHT),
        )
    )
    job_root = os.environ.get('MEDIA_SCRAPER_JOB_ROOT', str(DEFAULT_JOB_ROOT))

    server = create_server(
        host=host,
        port=port,
        job_root=job_root,
        ttl_seconds=ttl_seconds,
        max_active_jobs=max_active_jobs,
        workers=workers,
        max_download_bytes=max_download_mb * 1024 * 1024,
        max_video_height=max_video_height,
    )
    print('Media Scraper web app: http://{}:{}'.format(host, server.server_port))
    print('Press Ctrl+C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        server.executor.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    main()
