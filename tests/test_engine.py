"""Run: python -m unittest discover -s tests -v   (from repo root)"""
import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as engine  # noqa: E402  (engine code lives in app.py)
import app as webapp  # noqa: E402
from yt_dlp.utils import DownloadError  # noqa: E402


class Detection(unittest.TestCase):
    def test_platforms(self):
        self.assertEqual(engine.detect_platform('https://www.facebook.com/reel/1'), 'facebook')
        self.assertEqual(engine.detect_platform('https://fb.watch/abc'), 'facebook')
        self.assertEqual(engine.detect_platform('https://vm.tiktok.com/xyz/'), 'tiktok')
        self.assertEqual(engine.detect_platform('https://youtu.be/x'), 'youtube')
        self.assertEqual(engine.detect_platform('https://example.org/v'), 'generic')
        self.assertEqual(engine.detect_platform('funny cats'), 'search')

    def test_mobile_facebook_normalised(self):
        self.assertEqual(engine.normalize_url('https://m.facebook.com/watch/?v=1'),
                         'https://www.facebook.com/watch/?v=1')

    def test_friendly_errors_no_cookie_talk(self):
        self.assertNotIn('cookie', engine.friendly_error('Sign in to view this content, login required').lower())
        self.assertIn('পড়া যায়নি', engine.friendly_error('Facebook: Cannot parse data; please report'))

    def test_friendly_errors(self):
        self.assertIn('লগইন', engine.friendly_error('Log in for access'))
        self.assertIn('প্রাইভেট', engine.friendly_error('This video is private'))
        self.assertIn('সংযোগ', engine.friendly_error('TLS/SSL connection has been closed'))


class Strategies(unittest.TestCase):
    def test_facebook_has_multiple_strategies(self):
        names = [n for n, _ in engine.build_strategies('facebook', 'https://www.facebook.com/reel/1')]
        self.assertEqual(names[:2], ['facebook-desktop', 'facebook-mobile'])
        self.assertEqual(names[-1], 'generic-any')

    def test_extract_falls_through_to_working_strategy(self):
        calls = []

        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                calls.append(self.opts.get('http_headers', {}).get('User-Agent'))
                if len(calls) == 1:
                    raise DownloadError('Unable to download webpage: TLS EOF')
                return {'title': 'ok', 'url': 'https://cdn/x.mp4', 'protocol': 'https',
                        'vcodec': 'h264', 'acodec': 'aac'}

        with mock.patch.object(engine.yt_dlp, 'YoutubeDL', FakeYDL):
            info, name = engine.extract_info('https://www.facebook.com/reel/1', 'facebook')
        self.assertEqual(name, 'facebook-mobile')
        self.assertEqual(info['title'], 'ok')
        self.assertEqual(len(calls), 2)

    def test_youtube_has_multiple_clients(self):
        names = [n for n, _ in engine.build_strategies('youtube', 'https://youtu.be/x')]
        self.assertIn('youtube-ios', names)
        self.assertIn('youtube-mweb', names)

    def test_all_strategies_fail_gives_hint(self):
        class FailYDL(object):
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                raise DownloadError('Sign in to view this content, login required')

        with mock.patch.object(engine.yt_dlp, 'YoutubeDL', FailYDL):
            with self.assertRaises(engine.EngineError) as ctx:
                engine.extract_info('https://www.facebook.com/reel/1', 'facebook')
        self.assertIn('লগইন', str(ctx.exception))
        self.assertNotIn('Sign in', str(ctx.exception))  # raw yt-dlp text must not reach the user


class Routes(unittest.TestCase):
    def setUp(self):
        webapp.app.config['TESTING'] = True
        self.client = webapp.app.test_client()

    def test_health(self):
        r = self.client.get('/api/health')
        self.assertEqual(r.status_code, 200)
        self.assertIn('facebook', r.get_json()['platforms'])

    def test_direct_single_file_returns_proxy_url(self):
        info = {'title': 'Reel!', 'extractor': 'facebook', 'url': 'https://cdn.example/v.mp4',
                'protocol': 'https', 'vcodec': 'h264', 'acodec': 'aac', 'ext': 'mp4',
                'http_headers': {'Referer': 'https://www.facebook.com/'}}
        with mock.patch.object(engine, 'extract_info', return_value=(info, 'facebook-desktop')):
            r = self.client.post('/api/fetch', json={'url': 'https://www.facebook.com/reel/899595506527385/'})
        d = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(d['url'].startswith('/api/proxy_video?'))
        self.assertEqual(d['filename'], 'Reel.mp4')
        self.assertEqual(d['strategy'], 'facebook-desktop')

    def test_split_streams_creates_job_and_completes(self):
        info = {'title': 'Split', 'requested_formats': [{'url': 'a'}, {'url': 'b'}]}
        done = {'title': 'Split', 'url': '/static/downloads/x.mp4', 'filename': 'Split.mp4'}
        with mock.patch.object(engine, 'extract_info', return_value=(info, 'default')), \
             mock.patch.object(engine, 'download_to_server', return_value=done):
            r = self.client.post('/api/fetch', json={'url': 'https://www.tiktok.com/@a/video/1'})
            self.assertEqual(r.status_code, 202)
            jid = r.get_json()['job_id']
            for _ in range(100):
                j = self.client.get(f'/api/jobs/{jid}').get_json()
                if j['status'] in ('done', 'error'):
                    break
                time.sleep(0.05)
        self.assertEqual(j['status'], 'done')
        self.assertEqual(j['url'], '/static/downloads/x.mp4')

    def test_engine_error_is_returned_with_status(self):
        with mock.patch.object(engine, 'extract_info',
                               side_effect=engine.EngineError('ভিডিওটি প্রাইভেট', status=500)):
            r = self.client.post('/api/fetch', json={'url': 'https://www.facebook.com/reel/1'})
        self.assertEqual(r.status_code, 500)
        self.assertIn('প্রাইভেট', r.get_json()['error'])

    def test_proxy_rejects_non_http(self):
        r = self.client.get('/api/proxy_video?stream_url=file:///etc/passwd')
        self.assertEqual(r.status_code, 400)


class NewPipeServiceClient(unittest.TestCase):
    def setUp(self):
        webapp.app.config['TESTING'] = True
        self.client = webapp.app.test_client()

    def _resp(self, status, payload):
        m = mock.Mock(status_code=status, text=str(payload))
        m.json.return_value = payload
        return m

    def test_picks_highest_muxed_stream(self):
        payload = {'ok': True, 'title': 'Hi', 'duration': 5, 'thumbnail': '', 'uploader': 'u',
                   'webpage_url': 'https://youtu.be/x',
                   'streams': [{'url': 'https://g/a', 'height': 360, 'resolution': '360p'},
                               {'url': 'https://g/b', 'height': 720, 'resolution': '720p'}]}
        with mock.patch.object(webapp.requests, 'get', return_value=self._resp(200, payload)):
            res = webapp.newpipe_service_fetch('https://youtu.be/x', is_search=False)
        self.assertEqual(res['resolution'], '720p')
        self.assertTrue(res['url'].startswith('/api/proxy_video?'))
        self.assertIn('https%3A%2F%2Fg%2Fb', res['url'])

    def test_service_down_returns_none(self):
        import requests as rq
        with mock.patch.object(webapp.requests, 'get', side_effect=rq.ConnectionError('down')):
            self.assertIsNone(webapp.newpipe_service_fetch('funny cats', is_search=True))

    def test_error_payload_returns_none(self):
        with mock.patch.object(webapp.requests, 'get', return_value=self._resp(404, {'ok': False})):
            self.assertIsNone(webapp.newpipe_service_fetch('x', is_search=True))


if __name__ == '__main__':
    unittest.main()
