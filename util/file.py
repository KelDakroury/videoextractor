#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2018 Elvis Yu-Jing Lin <elvisyjlin@gmail.com>
# Licensed under the MIT License - https://opensource.org/licenses/MIT

import os
import re


UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')

def get_basename(filename):
    return filename.rsplit('.', 1)[0]

def get_extension(filename):
    return filename.rsplit('.', 1)[1]

def rename_file(filename, name):
    return '{}.{}'.format(name, get_extension(filename))

def safe_makedirs(path):
    if not os.path.exists(path):
        os.makedirs(path)


def safe_filename(value, fallback='media', max_length=160):
    value = str(value or '').strip()
    value = UNSAFE_FILENAME_RE.sub('-', value)
    value = re.sub(r'\s+', ' ', value).strip(' .-')
    if value in ('', '.', '..'):
        value = fallback

    if len(value) > max_length:
        stem, extension = os.path.splitext(value)
        stem_length = max(1, max_length - len(extension))
        value = stem[:stem_length].rstrip(' .-') + extension

    return value
