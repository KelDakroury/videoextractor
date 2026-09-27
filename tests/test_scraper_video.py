import unittest

from mediascrapers import _parse_general_media


class GeneralVideoParsingTests(unittest.TestCase):

    def test_finds_direct_hls_and_embedded_video(self):
        source = '''
        <html>
          <head><title>Video page</title></head>
          <body>
            <video><source src="https://cdn.example.com/master.m3u8"></video>
            <iframe src="https://player.vimeo.com/video/123"></iframe>
          </body>
        </html>
        '''
        title, media_urls, embed_urls = _parse_general_media(source)
        self.assertEqual(title, 'Video page')
        self.assertIn('https://cdn.example.com/master.m3u8', media_urls)
        self.assertIn('https://player.vimeo.com/video/123', embed_urls)


if __name__ == '__main__':
    unittest.main()
