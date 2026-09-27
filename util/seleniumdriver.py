#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2018 Elvis Yu-Jing Lin <elvisyjlin@gmail.com>
# Licensed under the MIT License - https://opensource.org/licenses/MIT

import platform
from os import chmod, makedirs, stat
from os.path import dirname, exists, join
from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService

SRC_URL_DICT = {
    'webdriver/phantomjsdriver_2.1.1_win32/phantomjs.exe': 'https://www.dropbox.com/s/y1sc5ujzhdqb9f4/phantomjs.exe?dl=1', 
    'webdriver/phantomjsdriver_2.1.1_mac64/phantomjs': 'https://www.dropbox.com/s/b6hmitsz1u4wc5w/phantomjs?dl=1', 
    'webdriver/phantomjsdriver_2.1.1_linux32/phantomjs': 'https://www.dropbox.com/s/xxka7isoskg53tr/phantomjs?dl=1', 
    'webdriver/phantomjsdriver_2.1.1_linux64/phantomjs': 'https://www.dropbox.com/s/dhuw71d9l5umk5m/phantomjs?dl=1', 
    'webdriver/chromedriver_2.39_win32/chromedriver.exe': 'https://www.dropbox.com/s/k8dibiirz35zjf0/chromedriver.exe?dl=1', 
    'webdriver/chromedriver_2.39_mac64/chromedriver': 'https://www.dropbox.com/s/jatcb8n8lqijat9/chromedriver?dl=1', 
    'webdriver/chromedriver_2.39_linux64/chromedriver': 'https://www.dropbox.com/s/vgyik5zsngpkck4/chromedriver?dl=1', 
    'webdriver/geckodriver_0.19.1_win32/geckodriver.exe': 'https://www.dropbox.com/s/s10tyhwc8z9nikg/geckodriver.exe?dl=1', 
    'webdriver/geckodriver_0.19.1_win64/geckodriver.exe': 'https://www.dropbox.com/s/r9zt6l9c7cn1pc8/geckodriver.exe?dl=1', 
    'webdriver/geckodriver_0.19.1_macos/geckodriver': 'https://www.dropbox.com/s/la2bfgdsdk2mrhj/geckodriver?dl=1', 
    'webdriver/geckodriver_0.19.1_linux32/geckodriver': 'https://www.dropbox.com/s/8qjr5n1i9jhmkmb/geckodriver?dl=1', 
    'webdriver/geckodriver_0.19.1_linux64/geckodriver': 'https://www.dropbox.com/s/b966sm5v98nmd5g/geckodriver?dl=1', 
    'webdriver/geckodriver_0.19.1_arm7hf/geckodriver': 'https://www.dropbox.com/s/k8dibiirz35zjf0/chromedriver.exe?dl=1'
}

def get(driverType, localDriver=True, path='.'):
    driverType = str(driverType).strip()
    normalized = driverType.lower()

    if normalized == 'phantomjs':
        raise RuntimeError(
            'PhantomJS is no longer supported by modern Selenium. '
            'Use the general scraper in requests mode, or select a supported '
            'browser driver such as Safari, Chrome, or Firefox.'
        )

    if normalized == 'chrome':
        chrome_options = ChromeOptions()
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--disable-infobars")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})

        kwargs = {'options': chrome_options}
        if localDriver:
            source = get_source('Chrome', path)
            kwargs['service'] = ChromeService(
                executable_path=source,
                log_output=join(path, 'chromedriver.log'),
            )
        return webdriver.Chrome(**kwargs)

    if normalized == 'firefox':
        firefox_options = FirefoxOptions()
        firefox_options.add_argument("--start-maximized")

        kwargs = {'options': firefox_options}
        if localDriver:
            source = get_source('Firefox', path)
            kwargs['service'] = FirefoxService(
                executable_path=source,
                log_output=join(path, 'geckodriver.log'),
            )
        return webdriver.Firefox(**kwargs)

    if normalized == 'safari':
        try:
            return webdriver.Safari()
        except SessionNotCreatedException as exc:
            message = str(exc)
            if 'Allow remote automation' in message or 'remote automation' in message:
                raise RuntimeError(
                    'Safari WebDriver is disabled. Enable it in Safari first:\n'
                    '1. Open Safari.\n'
                    '2. Safari -> Settings -> Advanced, then enable developer features if needed.\n'
                    '3. In the Develop menu, enable "Allow Remote Automation".\n'
                    '4. Retry with MEDIA_SCRAPER_DRIVER=safari.\n'
                    'If you do not need JavaScript rendering, run the scraper without '
                    'MEDIA_SCRAPER_DRIVER to use requests mode.'
                ) from exc
            raise

    raise Exception('Not supported driver type [%s].' % driverType)

def get_source(driverType, path='.'):
    driverType = str(driverType)
    os = platform.system()
    bits = platform.architecture()[0]
    source = None
    if driverType == 'PhantomJS':
        if os == 'Windows':
            source = join(path, 'webdriver/phantomjsdriver_2.1.1_win32/phantomjs.exe')
        elif os == 'Darwin':
            source = join(path, 'webdriver/phantomjsdriver_2.1.1_mac64/phantomjs')
        elif os == 'Linux' and bits == '32bit':
            source = join(path, 'webdriver/phantomjsdriver_2.1.1_linux32/phantomjs')
        elif os == 'Linux' and bits == '64bit':
            source = join(path, 'webdriver/phantomjsdriver_2.1.1_linux64/phantomjs')
        else:
            raise Exception('Failed to recognize your OS [%s / %s].' % (os, bits))
    elif driverType == 'Chrome':
        if os == 'Windows':
            source = join(path, 'webdriver/chromedriver_2.39_win32/chromedriver.exe')
        elif os == 'Darwin':
            source = join(path, 'webdriver/chromedriver_2.39_mac64/chromedriver')
        elif os == 'Linux':
            source = join(path, 'webdriver/chromedriver_2.39_linux64/chromedriver')
        else:
            raise Exception('Failed to recognize your OS [%s / %s].' % (os, bits))
    elif driverType == 'Firefox':
        if os == 'Windows' and bits == '32bit':
            source = join(path, 'webdriver/geckodriver_0.19.1_win32/geckodriver.exe')
        elif os == 'Windows' and bits == '64bit':
            source = join(path, 'webdriver/geckodriver_0.19.1_win64/geckodriver.exe')
        elif os == 'Darwin':
            source = join(path, 'webdriver/geckodriver_0.19.1_macos/geckodriver')
        elif os == 'Linux' and bits == '32bit':
            source = join(path, 'webdriver/geckodriver_0.19.1_linux32/geckodriver')
        elif os == 'Linux' and bits == '64bit':
            source = join(path, 'webdriver/geckodriver_0.19.1_linux64/geckodriver')
        else:
            raise Exception('Failed to recognize your OS [%s / %s].' % (os, bits))
    else:
        raise Exception('Not supported driver type [%s].' % driverType)
    if not exists(source):
        print('Web driver "%s" not found.' % source)
        global SRC_URL_DICT
        for (src, url) in SRC_URL_DICT.items():
            if src in source:
                print('Start downloading the web driver...')
                makedirs(dirname(source), exist_ok=True)
                # import urllib.request
                import requests
                import shutil
                from requests.packages.urllib3.exceptions import InsecureRequestWarning
                requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
                # u = urllib.request.urlopen(url)
                # data = u.read()
                # u.close()
                res = requests.get(url, stream=True)
                with open(source, "wb") as f:
                    # f.write(data)
                    shutil.copyfileobj(res.raw, f)
                st = stat(source)
                chmod(source, st.st_mode | 0o111)  # make it executable
                print('Web driver "%s" has been downloaded successfully.' % source)
    print(source)
    return source
