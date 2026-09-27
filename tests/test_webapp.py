import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from webapp import create_server


class WebAppTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.server = create_server(
            host='127.0.0.1',
            port=0,
            job_root=self.temp_dir.name,
            workers=1,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = 'http://127.0.0.1:{}'.format(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.executor.shutdown(wait=False, cancel_futures=True)
        self.thread.join(timeout=3)
        self.temp_dir.cleanup()

    def test_homepage_and_health_endpoint(self):
        with urllib.request.urlopen(self.base_url + '/') as response:
            homepage = response.read().decode('utf-8')
        self.assertIn('VideoExtractor', homepage)

        with urllib.request.urlopen(self.base_url + '/api/health') as response:
            health = json.loads(response.read().decode('utf-8'))
        self.assertEqual(health, {'status': 'ok'})

    def test_rejects_private_source_url(self):
        request = urllib.request.Request(
            self.base_url + '/api/jobs',
            data=json.dumps({'url': 'http://127.0.0.1/video'}).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request)
        self.assertEqual(context.exception.code, 400)
        context.exception.close()

    def test_serves_video_byte_ranges(self):
        job = self.server.job_store.create('https://example.com/video')
        internal = self.server.job_store.get_internal(job['id'])
        video_path = Path(internal['directory']) / 'sample.mp4'
        video_path.write_bytes(b'0123456789')
        self.server.job_store.complete(
            job['id'],
            'Sample',
            [{'name': 'sample.mp4', 'path': str(video_path), 'size': 10}],
        )

        request = urllib.request.Request(
            self.base_url + '/api/jobs/{}/files/0'.format(job['id']),
            headers={'Range': 'bytes=2-5'},
        )
        with urllib.request.urlopen(request) as response:
            body = response.read()
            content_range = response.headers['Content-Range']

        self.assertEqual(response.status, 206)
        self.assertEqual(content_range, 'bytes 2-5/10')
        self.assertEqual(body, b'2345')


if __name__ == '__main__':
    unittest.main()
