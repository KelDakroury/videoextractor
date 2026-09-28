import tempfile
import unittest
from unittest.mock import patch

from video_service import (
    VideoProcessingError,
    _provider_error,
    _provider_extractor_args,
    _provider_for_url,
    extract_videos,
)


class ProviderVideoTests(unittest.TestCase):

    def test_detects_supported_provider_hosts(self):
        self.assertEqual(
            _provider_for_url('https://www.youtube.com/watch?v=abc'),
            'YouTube',
        )
        self.assertEqual(
            _provider_for_url('https://youtu.be/abc'),
            'YouTube',
        )
        self.assertEqual(
            _provider_for_url('https://www.instagram.com/reel/abc/'),
            'Instagram',
        )
        self.assertEqual(
            _provider_for_url('https://www.bilibili.tv/en/video/123'),
            'Bilibili',
        )
        self.assertIsNone(
            _provider_for_url('https://youtube.com.example.org/watch?v=abc')
        )

    @patch('video_service._extract_with_ytdlp')
    def test_routes_provider_urls_through_ytdlp(self, extract_provider):
        expected = {
            'title': 'Provider video',
            'files': [],
        }
        extract_provider.return_value = expected

        with tempfile.TemporaryDirectory() as output_dir:
            result = extract_videos(
                'https://www.youtube.com/watch?v=abc',
                output_dir,
                max_download_bytes=123,
                max_video_height=480,
            )

        self.assertEqual(result, expected)
        extract_provider.assert_called_once_with(
            source_url='https://www.youtube.com/watch?v=abc',
            output_dir=output_dir,
            provider='YouTube',
            progress_callback=None,
            max_download_bytes=123,
            max_video_height=480,
        )

    def test_translates_provider_rate_limit_errors(self):
        error = _provider_error('Instagram', 'HTTP Error 429: Too Many Requests')
        self.assertIsInstance(error, VideoProcessingError)
        self.assertIn('rate-limited', str(error))

    def test_uses_cloud_compatible_youtube_client(self):
        self.assertEqual(
            _provider_extractor_args('YouTube'),
            {
                'youtube': {
                    'player_client': ['mweb'],
                },
            },
        )
        self.assertIsNone(_provider_extractor_args('Bilibili'))


if __name__ == '__main__':
    unittest.main()
