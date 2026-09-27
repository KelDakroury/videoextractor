#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os

from mediascrapers import MediaScraper
from util.file import safe_filename, safe_makedirs
from util.media import mux_audio_video
from util.url import download, is_video


DEFAULT_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
MAX_MEDIA_TRACKS = 20


class VideoNotFoundError(RuntimeError):
    pass


class VideoProcessingError(RuntimeError):
    pass


def _notify(callback, stage, progress, message):
    if callback is not None:
        callback(stage, progress, message)


def extract_videos(
    source_url,
    output_dir,
    progress_callback=None,
    max_download_bytes=DEFAULT_MAX_DOWNLOAD_BYTES,
):
    safe_makedirs(output_dir)
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
