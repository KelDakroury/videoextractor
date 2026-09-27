#!/usr/bin/env python
# -*- coding: utf-8 -*-

import ipaddress
import socket
import urllib.parse
import urllib.request


class UnsafeURLError(ValueError):
    pass


def validate_public_url(url, resolve=True):
    if not isinstance(url, str) or len(url) > 4096:
        raise UnsafeURLError('Enter a valid URL shorter than 4,096 characters.')

    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in ('http', 'https'):
        raise UnsafeURLError('Only http:// and https:// links are supported.')
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURLError('Links containing a username or password are not supported.')
    if parsed.hostname is None:
        raise UnsafeURLError('The link must include a valid hostname.')

    hostname = parsed.hostname.rstrip('.').lower()
    if hostname == 'localhost' or hostname.endswith('.localhost') or hostname.endswith('.local'):
        raise UnsafeURLError('Local and private network links are not allowed.')

    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError('The link contains an invalid port.') from exc

    expected_port = 443 if parsed.scheme == 'https' else 80
    if port not in (None, expected_port):
        raise UnsafeURLError('Only standard HTTP and HTTPS ports are allowed.')

    if resolve:
        try:
            addresses = socket.getaddrinfo(
                hostname,
                expected_port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise UnsafeURLError('The link hostname could not be found.') from exc

        if not addresses:
            raise UnsafeURLError('The link hostname could not be found.')

        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise UnsafeURLError('Local and private network links are not allowed.')

    return parsed.geturl()


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public_url(request_or_url, timeout=30):
    if isinstance(request_or_url, urllib.request.Request):
        url = request_or_url.full_url
    else:
        url = request_or_url

    validate_public_url(url)
    opener = urllib.request.build_opener(PublicRedirectHandler())
    return opener.open(request_or_url, timeout=timeout)
