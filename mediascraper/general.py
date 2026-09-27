#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2018 Elvis Yu-Jing Lin <elvisyjlin@gmail.com>
# Licensed under the MIT License - https://opensource.org/licenses/MIT

import mediascrapers
import os
import sys

if __name__ == '__main__':
    driver = os.environ.get('MEDIA_SCRAPER_DRIVER', 'requests')
    scraper = mediascrapers.MediaScraper(mode='normal', debug=False, driver=driver)
    for url in sys.argv[1:]:
        tasks = scraper.scrape(url)
        scraper.download(tasks=tasks, path='download/general')
