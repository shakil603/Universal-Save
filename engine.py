"""Universal Save download engine.

- platform detection and URL normalisation
- multiple yt-dlp strategies (tried in order until one works)
- optional cookies (Facebook / Instagram / private content) via env var
- background download jobs for files that must be merged/transcoded on the server
- human readable (Bengali) error hints
"""
import base64
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

logger = logging.getLogger('universal_save.engine')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'static', 'downloads')
COOKIE_FILE = os.path.join(BASE_DIR, 'cookies.txt')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

DESKTOP_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
MOBILE_UA = ('Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36')

FILE_TTL = 60 * 60          # সার্ভারে তৈরি ফাইল ১ ঘণ্টা
JOB_TTL = 2 * 60 * 60       # জব রেকর্ড ২ ঘণ্টা
MAX_FILESIZE = 500 * 1024 * 1024  # 500 MB সীমা

# ------------------------------------------------------------------ platforms
PLATFORMS = {
    'youtube':   ['youtube.com', 'youtu.be', 'music.youtube.com'],
    'facebook':  ['facebook.com', 'fb.watch', 'fb.com'],
    'instagram': ['instagram.com', 'instagr.am'],
    'tiktok':    ['tiktok.com', 'vm.tiktok.com'],
    'twitter':   ['twitter.com', 'x.com', 't.co'],
    'vimeo':     ['vimeo.com'],
    'reddit':    ['reddit.com', 'redd.it'],
    'dailymotion': ['dailymotion.com', 'dai.ly'],
    'pinterest': ['pinterest.com', 'pin.it'],
    'snapchat':  ['snapchat.com'],
    'linkedin':  ['linkedin.com'],
    'twitch':    ['twitch.tv'],
    'soundcloud': ['soundcloud.com'],
}


def is_url(text):
    return text.startswith(('http://', 'https://'))


def detect_platform(url):
    if not is_url(url):
        return 'search'
    host = urlparse(url).netloc.lower().split(':')[0]
    for name, domains in PLATFORMS.items():
        if any(host == d or host.endswith('.' + d) for d in domains):
            return name
    return 'generic'


def normalize_url(url):
    """মোবাইল/বিকল্প সাবডোমেইন ডেস্কটপ ফর্মে আনা (Facebook ইত্যাদি)।"""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith(('m.', 'mbasic.', 'web.')) and host.endswith('facebook.com'):
        return url.replace(parsed.netloc, 'www.facebook.com', 1)
    return url


# ------------------------------------------------------------------ cookies
def ensure_cookie_file():
    """Render/Docker-এ env var থেকে cookies.txt বানানো।

    YTDLP_COOKIES_B64 = base64 করা Netscape cookies.txt
    অথবা repo/ডিরেক্টরিতে cookies.txt থাকলে সেটি ব্যবহার।
    """
    b64 = os.environ.get('YTDLP_COOKIES_B64', '').strip()
    if b64:
        try:
            data = base64.b64decode(b64).decode('utf-8')
            with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
                f.write(data)
        except Exception as e:
            logger.error(f"YTDLP_COOKIES_B64 decode failed: {e}")
    return COOKIE_FILE if os.path.exists(COOKIE_FILE) and os.path.getsize(COOKIE_FILE) > 0 else None


# ------------------------------------------------------------------ strategies
SINGLE_FILE_FORMAT = ('best[vcodec!=none][acodec!=none][ext=mp4]/'
                      'best[vcodec!=none][acodec!=none]/best')


def build_strategies(platform, url):
    """প্ল্যাটফর্ম অনুযায়ী চেষ্টা করার opts-এর তালিকা (ক্রমানুসারে)।"""
    base = {
        'nocheckcertificate': True,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'socket_timeout': 30,
        'retries': 3,
        'max_filesize': MAX_FILESIZE,
    }
    cookies = ensure_cookie_file()
    strategies = []

    if platform == 'youtube':
        strategies.append(('youtube-android', {'extractor_args': {'youtube': {'player_client': ['android', 'web']}}}))
    if platform == 'facebook':
        strategies.append(('facebook-desktop', {'http_headers': {'User-Agent': DESKTOP_UA}}))
        strategies.append(('facebook-mobile', {'http_headers': {'User-Agent': MOBILE_UA}}))
    if platform in ('instagram', 'tiktok', 'twitter', 'snapchat'):
        strategies.append((f'{platform}-mobile', {'http_headers': {'User-Agent': MOBILE_UA}}))
    strategies.append(('default', {}))
    strategies.append(('generic-any', {'format': 'best'}))

    result = []
    for name, extra in strategies:
        opts = dict(base)
        opts['format'] = SINGLE_FILE_FORMAT
        opts.update(extra)
        if cookies and platform in ('facebook', 'instagram', 'youtube', 'tiktok', 'twitter', 'generic'):
            opts['cookiefile'] = cookies
        result.append((name, opts))
    return result


# ------------------------------------------------------------------ errors
HINTS = [
    (('login', 'log in', 'sign in', 'cookies', 'authentication', 'not logged'),
     'এই সাইটে লগইন প্রয়োজন। সার্ভারে YTDLP_COOKIES_B64 সেট করুন (আপনার ব্রাউজারের cookies.txt)।'),
    (('private', 'members-only', 'members only'),
     'ভিডিওটি প্রাইভেট বা শুধু সদস্যদের জন্য।'),
    (('not available in your country', 'geo', 'region'),
     'ভিডিওটি আপনার অঞ্চলে উপলব্ধ নয়।'),
    (('unsupported url',),
     'এই লিংকটি সাপোর্ট করা হয় না।'),
    (('video unavailable', 'removed', 'does not exist', 'not found', '404'),
     'ভিডিওটি পাওয়া যায়নি বা মুছে ফেলা হয়েছে।'),
    (('sign in to confirm', 'bot'),
     'সাইটটি বট হিসেবে সনাক্ত করেছে। কিছুক্ষণ পরে আবার চেষ্টা করুন অথবা cookies দিন।'),
    (('tls', 'ssl', 'timed out', 'timeout', 'connection', 'unable to download webpage'),
     'সাইটের সাথে সংযোগে সমস্যা হয়েছে। একটু পরে আবার চেষ্টা করুন।'),
]


def friendly_error(message):
    low = (message or '').lower()
    for keys, hint in HINTS:
        if any(k in low for k in keys):
            return hint
    return 'ভিডিও ডাউনলোড করা যায়নি।'


class EngineError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ extraction
def extract_info(target, platform, download=False, extra_opts=None):
    """সব strategy চেষ্টা করে প্রথম সফল info ফেরত দেয়।"""
    errors = []
    for name, opts in build_strategies(platform, target):
        if extra_opts:
            opts.update(extra_opts)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=download)
            if info:
                logger.info(f"[{platform}] strategy '{name}' succeeded")
                return info, name
            errors.append(f'{name}: empty result')
        except DownloadError as e:
            logger.warning(f"[{platform}] strategy '{name}' failed: {e}")
            errors.append(f'{name}: {e}')
        except Exception as e:  # নেটওয়ার্ক/অপ্রত্যাশিত
            logger.warning(f"[{platform}] strategy '{name}' crashed: {e}")
            errors.append(f'{name}: {e}')
    combined = ' | '.join(errors)
    raise EngineError(friendly_error(combined) + f' [{combined[:300]}]', status=404 if 'not found' in combined.lower() else 500)


def first_entry(info):
    if info and 'entries' in info:
        entries = [e for e in info['entries'] if e]
        if not entries:
            raise EngineError('কোনো ভিডিও পাওয়া যায়নি', status=404)
        return entries[0]
    return info


def _is_http(fmt):
    return (fmt.get('protocol') or 'https') in ('http', 'https') and bool(fmt.get('url'))


def _has_av(fmt):
    return fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none'


def pick_direct_format(info):
    """একটি ফাইলে ভিডিও+অডিও আছে এমন সরাসরি HTTP লিংক।"""
    if _is_http(info) and _has_av(info):
        return info
    candidates = [f for f in (info.get('formats') or []) if _is_http(f) and _has_av(f)]
    if not candidates:
        return None
    candidates.sort(key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))
    return candidates[-1]


def safe_filename(title):
    name = ''.join(c for c in (title or '') if c.isalnum() or c in (' ', '-', '_')).strip()[:80]
    return (name or 'video') + '.mp4'


# ------------------------------------------------------------------ files
def cleanup_files():
    now = time.time()
    for name in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > FILE_TTL:
                os.remove(path)
        except OSError:
            pass


# ------------------------------------------------------------------ jobs
class JobManager:
    def __init__(self, workers=2):
        self._jobs = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='dl')

    def _gc(self):
        now = time.time()
        with self._lock:
            for jid in [j for j, v in self._jobs.items() if now - v['created'] > JOB_TTL]:
                del self._jobs[jid]

    def submit(self, url, platform):
        self._gc()
        cleanup_files()
        jid = uuid.uuid4().hex
        with self._lock:
            self._jobs[jid] = {'id': jid, 'status': 'queued', 'created': time.time(),
                               'url': url, 'result': None, 'error': None}
        self._pool.submit(self._run, jid, url, platform)
        return jid

    def get(self, jid):
        with self._lock:
            job = self._jobs.get(jid)
            return dict(job) if job else None

    def _set(self, jid, **kw):
        with self._lock:
            self._jobs[jid].update(kw)

    def _run(self, jid, url, platform):
        self._set(jid, status='running')
        try:
            self._set(jid, status='done', result=download_to_server(url, platform, jid))
        except EngineError as e:
            self._set(jid, status='error', error=str(e))
        except Exception as e:
            logger.exception('download job failed')
            self._set(jid, status='error', error=friendly_error(str(e)))


def download_to_server(url, platform, jid):
    """HLS/DASH বা আলাদা অডিও-ভিডিও হলে ffmpeg দিয়ে মার্জ করে ফাইল বানানো।"""
    stem = f"{int(time.time())}_{jid[:10]}"
    extra = {
        'format': 'bestvideo*+bestaudio/best',
        'merge_output_format': 'mp4',
        'outtmpl': os.path.join(DOWNLOAD_DIR, stem + '.%(ext)s'),
    }
    info, _ = extract_info(url, platform, download=True, extra_opts=extra)
    info = first_entry(info)
    files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(stem + '.')]
    if not files:
        raise EngineError('ফাইল তৈরি হয়নি', status=500)
    name = files[0]
    title = info.get('title', 'Video')
    return {
        'title': title,
        'thumbnail': info.get('thumbnail', ''),
        'duration': info.get('duration', 0),
        'uploader': info.get('uploader', 'Unknown'),
        'ext': os.path.splitext(name)[1].lstrip('.') or 'mp4',
        'platform': info.get('extractor', platform),
        'url': f'/static/downloads/{name}',
        'original_url': url,
        'filename': safe_filename(title),
    }


jobs = JobManager()


def list_platforms():
    return sorted(PLATFORMS.keys())
