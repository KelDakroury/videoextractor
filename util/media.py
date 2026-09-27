#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import platform
import shutil
import subprocess
from os.path import abspath, dirname, join


def mux_audio_video(video_file, audio_file, output_file, replace=False):
    if not os.path.exists(video_file) or not os.path.exists(audio_file):
        return False

    if not replace and os.path.exists(output_file):
        print('The file {} exists. Skip it.'.format(output_file))
        return False

    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is not None:
        cmd = [
            ffmpeg,
            '-y' if replace else '-n',
            '-i', video_file,
            '-i', audio_file,
            '-c', 'copy',
            output_file,
        ]
    elif platform.system() == 'Darwin':
        swift = shutil.which('swift')
        script = join(dirname(__file__), 'mux_av.swift')
        if swift is None or not os.path.exists(script):
            print('No local mux tool is available to merge {} and {}.'.format(video_file, audio_file))
            return False
        module_cache_path = abspath(join(dirname(__file__), '..', '.swift-module-cache'))
        os.makedirs(module_cache_path, exist_ok=True)
        cmd = [
            swift,
            '-module-cache-path', module_cache_path,
            script,
            video_file,
            audio_file,
            output_file,
        ]
    else:
        print('No local mux tool is available to merge {} and {}.'.format(video_file, audio_file))
        return False

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print('Merged embedded video to {}.'.format(output_file))
        return True
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        print('Failed to merge {} and {}: {}'.format(video_file, audio_file, message))
        return False
