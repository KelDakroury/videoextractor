import socket
import unittest
from unittest import mock

from util.network import UnsafeURLError, validate_public_url


class ValidatePublicURLTests(unittest.TestCase):

    def test_accepts_public_https_url(self):
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))
        with mock.patch('util.network.socket.getaddrinfo', return_value=[address]):
            result = validate_public_url('https://example.com/video')
        self.assertEqual(result, 'https://example.com/video')

    def test_rejects_localhost_without_dns_lookup(self):
        with mock.patch('util.network.socket.getaddrinfo') as getaddrinfo:
            with self.assertRaises(UnsafeURLError):
                validate_public_url('http://localhost/video')
        getaddrinfo.assert_not_called()

    def test_rejects_private_resolved_address(self):
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.8', 443))
        with mock.patch('util.network.socket.getaddrinfo', return_value=[address]):
            with self.assertRaises(UnsafeURLError):
                validate_public_url('https://internal.example/video')

    def test_rejects_nonstandard_port(self):
        with self.assertRaises(UnsafeURLError):
            validate_public_url('https://example.com:8443/video', resolve=False)

    def test_rejects_credentials(self):
        with self.assertRaises(UnsafeURLError):
            validate_public_url('https://user:password@example.com/video', resolve=False)


if __name__ == '__main__':
    unittest.main()
