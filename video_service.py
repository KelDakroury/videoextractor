#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shutil
import urllib.parse

from mediascrapers import MediaScraper
from util.file import safe_filename, safe_makedirs
from util.media import mux_audio_video
from util.url import DownloadTooLargeError, download, is_video


DEFAULT_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_VIDEO_HEIGHT = 720
MAX_MEDIA_TRACKS = 20
YOUTUBE_HOSTS = ('youtube.com', 'youtube-nocookie.com')
INSTAGRAM_HOSTS = ('instagram.com', 'instagr.am')
BILIBILI_HOSTS = ('bilibili.com', 'bilibili.tv', 'b23.tv')
BOUNDED_FORMAT_PROVIDERS = ('YouTube', 'Bilibili')


class VideoNotFoundError(RuntimeError):
    pass


class VideoProcessingError(RuntimeError):
    pass


class _YTDLPLogger:

    def __init__(self):
        self.warnings = []
        self.errors = []

    def debug(self, message):
        message = str(message)
        if 'max-filesize' in message.lower():
            self.warnings.append(message)

    def info(self, message):
        pass

    def warning(self, message):
        self.warnings.append(str(message))

    def error(self, message):
        self.errors.append(str(message))

    def last_message(self):
        messages = self.errors or self.warnings
        return messages[-1] if messages else ''


def _notify(callback, stage, progress, message):
    if callback is not None:
        callback(stage, progress, message)


def _host_matches(hostname, domains):
    return any(
        hostname == domain or hostname.endswith('.' + domain)
        for domain in domains
    )


def _provider_for_url(source_url):
    hostname = (urllib.parse.urlsplit(source_url).hostname or '').lower().rstrip('.')
    if hostname == 'youtu.be' or _host_matches(hostname, YOUTUBE_HOSTS):
        return 'YouTube'
    if _host_matches(hostname, INSTAGRAM_HOSTS):
        return 'Instagram'
    if _host_matches(hostname, BILIBILI_HOSTS):
        return 'Bilibili'
    return None


def _download_limit_message(max_download_bytes):
    return 'The video is larger than the {} MB download limit.'.format(
        max_download_bytes // (1024 * 1024)
    )


def _provider_error(provider, message):
    lowered = message.lower()
    if 'unsupported url' in lowered:
        return VideoNotFoundError(
            '{} did not recognize this video URL.'.format(provider)
        )
    if 'private' in lowered or 'login required' in lowered or 'sign in' in lowered:
        return VideoProcessingError(
            '{} requires authentication for this video. '
            'Only publicly accessible videos are supported.'.format(provider)
        )
    if (
        'http error 429' in lowered
        or 'too many requests' in lowered
        or 'rate limit' in lowered
    ):
        return VideoProcessingError(
            '{} temporarily rate-limited this server. Please try again later.'.format(
                provider
            )
        )
    if 'not available' in lowered or 'removed' in lowered:
        return VideoNotFoundError(
            'This {} video is unavailable or has been removed.'.format(provider)
        )
    return VideoProcessingError(
        '{} could not provide a downloadable video. It may be restricted, '
        'age-gated, or temporarily unavailable.'.format(provider)
    )


def _clear_output_directory(output_dir):
    for name in os.listdir(output_dir):
        path = os.path.join(output_dir, name)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            pass


def _bounded_provider_format(max_video_height):
    return (
        'bv*[height<={height}][ext=mp4]+ba[ext=m4a]/'
        'b[height<={height}][ext=mp4]/'
        'bv*[height<={height}]+ba/b[height<={height}]'
    ).format(height=max_video_height)


def _provider_extractor_args(provider):
    if provider == 'YouTube':
        # The mweb client exposes a progressive MP4 on cloud hosts where
        # YouTube blocks the default clients unless a PO token is available.
        return {
            'youtube': {
                'player_client': ['mweb'],
            },
        }
    return None


def _extract_with_ytdlp(
    source_url,
    output_dir,
    provider,
    progress_callback,
    max_download_bytes,
    max_video_height,
):
    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError
    except ImportError as exc:
        raise VideoProcessingError(
            '{} support is not installed on this server.'.format(provider)
        ) from exc

    logger = _YTDLPLogger()
    downloaded_by_file = {}
    finished_files = set()
    size_limit_exceeded = [False]
    last_progress = [24]

    def progress_hook(status):
        info = status.get('info_dict') or {}
        requested_formats = info.get('requested_formats') or [info]
        track_count = max(1, len(requested_formats))
        file_key = (
            info.get('format_id')
            or status.get('tmpfilename')
            or status.get('filename')
            or 'download'
        )
        downloaded_bytes = int(status.get('downloaded_bytes') or 0)
        if downloaded_bytes:
            downloaded_by_file[file_key] = max(
                downloaded_bytes,
                downloaded_by_file.get(file_key, 0),
            )
            if sum(downloaded_by_file.values()) > max_download_bytes:
                size_limit_exceeded[0] = True
                raise DownloadTooLargeError(
                    _download_limit_message(max_download_bytes)
                )

        state = status.get('status')
        if state == 'finished':
            finished_files.add(file_key)
            fraction = min(1.0, len(finished_files) / track_count)
        elif state == 'downloading':
            total_bytes = (
                status.get('total_bytes')
                or status.get('total_bytes_estimate')
                or 0
            )
            current_fraction = (
                min(1.0, downloaded_bytes / total_bytes)
                if total_bytes
                else 0.0
            )
            fraction = min(
                1.0,
                (len(finished_files) + current_fraction) / track_count,
            )
        else:
            return

        progress = max(last_progress[0], 24 + int(fraction * 56))
        last_progress[0] = progress
        _notify(
            progress_callback,
            'downloading',
            progress,
            'Downloading {} video'.format(provider),
        )

    def postprocessor_hook(status):
        if status.get('status') == 'started':
            _notify(
                progress_callback,
                'processing',
                84,
                'Combining video and audio',
            )
        elif status.get('status') == 'finished':
            _notify(
                progress_callback,
                'processing',
                92,
                'Finalizing your video',
            )

    options = {
        'cachedir': False,
        'concurrent_fragment_downloads': 2,
        'extractor_retries': 2,
        'file_access_retries': 2,
        'format': (
            _bounded_provider_format(max_video_height)
            if provider in BOUNDED_FORMAT_PROVIDERS
            else 'b[ext=mp4]/b'
        ),
        'fragment_retries': 3,
        'js_runtimes': {
            'deno': {},
            'node': {},
        },
        'logger': logger,
        'max_filesize': max_download_bytes,
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'noprogress': True,
        'outtmpl': {
            'default': os.path.join(
                output_dir,
                '%(title).140S [%(id)s].%(ext)s',
            ),
        },
        'overwrites': True,
        'playlistend': 1,
        'postprocessor_hooks': [postprocessor_hook],
        'progress_hooks': [progress_hook],
        'quiet': True,
        'retries': 3,
        'socket_timeout': 30,
        'trim_file_name': 150,
        'windowsfilenames': True,
    }
    if provider in BOUNDED_FORMAT_PROVIDERS:
        options['format_sort'] = [
            'vcodec:h264',
            'res:{}'.format(max_video_height),
            'acodec:aac',
        ]
    extractor_args = _provider_extractor_args(provider)
    if extractor_args is not None:
        options['extractor_args'] = extractor_args

    _notify(
        progress_callback,
        'extracting',
        14,
        'Resolving the {} video'.format(provider),
    )

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(source_url, download=True)
    except DownloadError as exc:
        message = logger.last_message() or str(exc)
        _clear_output_directory(output_dir)
        if size_limit_exceeded[0] or 'max-filesize' in message.lower():
            raise DownloadTooLargeError(
                _download_limit_message(max_download_bytes)
            ) from exc
        raise _provider_error(provider, message) from exc
    except DownloadTooLargeError:
        _clear_output_directory(output_dir)
        raise
    except Exception:
        _clear_output_directory(output_dir)
        raise

    files = []
    total_bytes = 0
    for root, _, filenames in os.walk(output_dir):
        for filename in filenames:
            if filename.endswith(('.part', '.ytdl', '.temp')):
                continue
            path = os.path.realpath(os.path.join(root, filename))
            if not is_video(path):
                continue
            size = os.path.getsize(path)
            total_bytes += size
            files.append(
                {
                    'name': os.path.basename(path),
                    'path': path,
                    'size': size,
                }
            )

    if total_bytes > max_download_bytes:
        _clear_output_directory(output_dir)
        raise DownloadTooLargeError(
            _download_limit_message(max_download_bytes)
        )
    if not files:
        message = logger.last_message()
        _clear_output_directory(output_dir)
        if 'max-filesize' in message.lower():
            raise DownloadTooLargeError(
                _download_limit_message(max_download_bytes)
            )
        raise _provider_error(provider, message)

    title = info.get('title') if isinstance(info, dict) else None
    return {
        'title': str(title or '{} video'.format(provider))[:300],
        'files': files,
    }


def extract_videos(
    source_url,
    output_dir,
    progress_callback=None,
    max_download_bytes=DEFAULT_MAX_DOWNLOAD_BYTES,
    max_video_height=DEFAULT_MAX_VIDEO_HEIGHT,
):
    safe_makedirs(output_dir)
    provider = _provider_for_url(source_url)
    if provider is not None:
        return _extract_with_ytdlp(
            source_url=source_url,
            output_dir=output_dir,
            provider=provider,
            progress_callback=progress_callback,
            max_download_bytes=max_download_bytes,
            max_video_height=max_video_height,
        )

    _notify(progress_callback, 'extracting', 12, 'Opening the page')

    scraper = MediaScraper(
        mode='silent',
        driver='requests',
        safe_network=True,
    )
    tasks = scraper.scrape_videos(source_url)
    if not tasks:
        raise VideoNotFoundError(
            'No downloadable video was found on this page. '
            'The player may use an unsupported provider or protected media.'
        )
    if len(tasks) > MAX_MEDIA_TRACKS:
        raise VideoProcessingError(
            'This page contains too many video sources to process safely.'
        )

    mux_tasks = scraper.embedded_video_mux_tasks()
    component_names = {
        component
        for mux_task in mux_tasks
        for component in (mux_task['video'], mux_task['audio'])
    }

    _notify(
        progress_callback,
        'downloading',
        30,
        'Video found. Preparing the download',
    )

    downloaded = []
    downloaded_bytes = 0
    for index, (media_url, _, rename) in enumerate(tasks, start=1):
        remaining_bytes = max_download_bytes - downloaded_bytes
        if remaining_bytes <= 0:
            raise VideoProcessingError('The video exceeds the download size limit.')

        progress = 30 + int((index - 1) / max(1, len(tasks)) * 48)
        _notify(
            progress_callback,
            'downloading',
            progress,
            'Downloading media track {} of {}'.format(index, len(tasks)),
        )
        downloaded_file = download(
            media_url,
            path=output_dir,
            rename=rename,
            replace=True,
            headers={'Referer': scraper.current_url()},
            max_bytes=remaining_bytes,
            safe_network=True,
            raise_errors=True,
        )
        if downloaded_file is None:
            raise VideoProcessingError('A video track could not be downloaded.')
        downloaded.append(downloaded_file)
        downloaded_bytes += os.path.getsize(downloaded_file)

    result_files = []
    for mux_task in mux_tasks:
        _notify(
            progress_callback,
            'processing',
            84,
            'Combining video and audio',
        )
        video_file = os.path.join(output_dir, mux_task['video'])
        audio_file = os.path.join(output_dir, mux_task['audio'])
        output_file = os.path.join(
            output_dir,
            safe_filename(mux_task['output'], fallback='video.mp4'),
        )
        if not mux_audio_video(video_file, audio_file, output_file, replace=True):
            raise VideoProcessingError(
                'The video tracks were downloaded, but they could not be combined. '
                'Install ffmpeg when running this service outside macOS.'
            )
        result_files.append(output_file)

        for component_file in (video_file, audio_file):
            if os.path.exists(component_file):
                os.remove(component_file)

    for downloaded_file in downloaded:
        filename = os.path.basename(downloaded_file)
        if filename in component_names or not os.path.exists(downloaded_file):
            continue
        if is_video(downloaded_file):
            result_files.append(downloaded_file)

    unique_files = []
    seen = set()
    for result_file in result_files:
        real_path = os.path.realpath(result_file)
        if real_path not in seen and os.path.exists(real_path):
            seen.add(real_path)
            unique_files.append(real_path)

    if not unique_files:
        raise VideoNotFoundError('No playable video could be produced from this page.')

    _notify(progress_callback, 'processing', 94, 'Finalizing your video')
    return {
        'title': scraper.page_title()[:300],
        'files': [
            {
                'name': os.path.basename(path),
                'path': path,
                'size': os.path.getsize(path),
            }
            for path in unique_files
        ],
    }
