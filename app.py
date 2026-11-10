import os
import json
import base64
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import logging
import time
import datetime
import requests
from flask import Flask, request, jsonify, render_template, Response, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
from urllib.parse import urlencode, urlparse
import yt_dlp
from yt_dlp.utils import DownloadError

app = Flask(__name__, template_folder='.', static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'universal_save_super_secret_key')
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- PUBLIC LIFETIME PERSISTENCE CONFIG ---
DATA_FILE = os.environ.get('DATA_FILE', 'marketplace_data.json')
UPLOAD_FOLDER = os.path.join('static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs('uploads', exist_ok=True)

def load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if 'products' not in data: data['products'] = []
                if 'orders' not in data: data['orders'] = []
                return data
        except Exception as e:
            logger.error(f"DB load error: {e}")
    return {"products": [], "orders": []}

def save_db(data):
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"DB save error: {e}")

@app.route('/')
def index():
    for fname in ['index_1.html', 'Index-1.html', 'index.html', 'Index_1.html']:
        if os.path.exists(fname):
            return render_template(fname)
    return "index_1.html not found", 404

@app.route('/static/uploads/<path:filename>')
def uploaded_files(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/uploads/<path:filename>')
def uploads_compat(filename):
    if os.path.exists(os.path.join('uploads', filename)):
        return send_from_directory('uploads', filename)
    return send_from_directory(UPLOAD_FOLDER, filename)

# --- PUBLIC API ---
@app.route('/api/data', methods=['GET'])
def get_data():
    db = load_db()
    resp = jsonify(db)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/api/add-product', methods=['POST'])
def add_product():
    try:
        name = request.form.get('name')
        price = request.form.get('price')
        image_file = request.files.get('image')

        if not name or not price or not image_file:
            return jsonify({'success': False, 'error': 'সব তথ্য দিন'}), 400

        filename = secure_filename(image_file.filename)
        filename = f"{int(time.time())}_{filename}"
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(image_path)

        db = load_db()
        new_product = {
            "id": int(time.time() * 1000),
            "name": name.strip(),
            "price": price.strip(),
            "img": f"/{image_path}",
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "createdAt": datetime.datetime.now().isoformat()
        }
        db['products'].insert(0, new_product)
        save_db(db)
        return jsonify({'success': True, 'product': new_product})
    except Exception as e:
        logger.error(f"Add product error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/delete-product/<int:prod_id>', methods=['DELETE'])
def delete_product(prod_id):
    try:
        db = load_db()
        for p in db['products']:
            if p['id'] == prod_id:
                img_path = p['img'].lstrip('/')
                if os.path.exists(img_path):
                    try: os.remove(img_path)
                    except: pass
                break
        db['products'] = [p for p in db['products'] if p['id'] != prod_id]
        save_db(db)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/place-order', methods=['POST'])
def place_order():
    try:
        data = request.get_json() or {}
        item = data.get('item')
        phone = data.get('phone')
        if not item or not phone:
            return jsonify({'success': False, 'error': 'তথ্য অসম্পূর্ণ'}), 400
        db = load_db()
        db['orders'].insert(0, {
            "item": item,
            "phone": phone,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        save_db(db)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# --- NEWPIPE / PIPED API INTEGRATION FOR YOUTUBE (WITH MULTIPLE FALLBACKS) ---
def fetch_via_newpipe(url_or_keyword):
    """
    একাধিক ওপেন সোর্স Piped (NewPipe Extractor) ইনস্ট্যান্স ব্যবহার করে ইউটিউব ভিডিও ফেচ করার ফাংশন।
    """
    piped_instances = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.drgns.space",
        "https://piped-api.privacy.com.de",
        "https://api.piped.privacy.coffee"
    ]

    try:
        # যদি এটি কিউওয়ার্ড বা সার্চ টেক্সট হয়
        if not url_or_keyword.startswith(('http://', 'https://')):
            for base_url in piped_instances:
                try:
                    search_url = f"{base_url}/search?q={requests.utils.quote(url_or_keyword)}&filter=videos"
                    res = requests.get(search_url, timeout=8)
                    if res.status_code == 200:
                        res_json = res.json()
                        if res_json and isinstance(res_json, dict):
                            items = res_json.get('items', [])
                            if items and isinstance(items, list):
                                vid_url = items[0].get('url', '')
                                if '=' in vid_url:
                                    vid = vid_url.split('=')[-1]
                                    url_or_keyword = f"https://www.youtube.com/watch?v={vid}"
                                    break
                except:
                    continue

        # ভিডিও আইডি বের করা
        vid_id = ""
        if "youtu.be/" in url_or_keyword:
            parts = url_or_keyword.split("youtu.be/")
            if len(parts) > 1:
                vid_id = parts[1].split("?")[0].split("&")[0]
        elif "watch?v=" in url_or_keyword:
            parts = url_or_keyword.split("watch?v=")
            if len(parts) > 1:
                vid_id = parts[1].split("&")[0].split("/")[0]

        if not vid_id:
            return None

        # একাধিক ইনস্ট্যান্স ট্রাই করা
        for base_url in piped_instances:
            try:
                piped_info_url = f"{base_url}/streams/{vid_id}"
                r = requests.get(piped_info_url, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if not data or not isinstance(data, dict):
                        continue

                    video_streams = data.get('videoStreams', [])
                    best_stream_url = None
                    if video_streams and isinstance(video_streams, list):
                        for s in video_streams:
                            if isinstance(s, dict) and s.get('videoOnly') == False and s.get('url'):
                                best_stream_url = s.get('url')
                                break
                        
                        if not best_stream_url and video_streams:
                            first_s = video_streams[0]
                            if isinstance(first_s, dict):
                                best_stream_url = first_s.get('url')

                    if best_stream_url:
                        title = data.get('title', 'YouTube Video')
                        safe_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()[:80]

                        return {
                            'success': True,
                            'title': title,
                            'thumbnail': data.get('thumbnailUrl', ''),
                            'duration': data.get('duration', 0),
                            'uploader': data.get('uploader', 'Unknown'),
                            'ext': 'mp4',
                            'platform': 'youtube_newpipe',
                            'video_url': build_proxy_url(best_stream_url),
                            'url': build_proxy_url(best_stream_url),
                            'original_url': url_or_keyword,
                            'filename': f"{safe_title}.mp4"
                        }
            except:
                continue
    except Exception as e:
        logger.error(f"NewPipe Multi-Instance API Error: {e}")
    return None

# === DOWNLOAD ENGINE (platforms, strategies, jobs, cookies) ===
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
        for client in ('ios', 'mweb', 'tv_embedded', 'web'):
            strategies.append((f'youtube-{client}',
                               {'extractor_args': {'youtube': {'player_client': [client]}}}))
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
    (('cannot parse data',),
     'সাইটটির তথ্য পড়া যায়নি। সার্ভারের ডাউনলোডার আপডেট প্রয়োজন, অথবা কিছুক্ষণ পরে আবার চেষ্টা করুন।'),
    (('login', 'log in', 'sign in to view', 'authentication', 'not logged'),
     'এই ভিডিওটি ডাউনলোড করতে লগইন প্রয়োজন, তাই সার্ভার থেকে নামানো যাচ্ছে না।'),
    (('private', 'members-only', 'members only'),
     'ভিডিওটি প্রাইভেট বা শুধু সদস্যদের জন্য।'),
    (('not available in your country', 'geo', 'region'),
     'ভিডিওটি আপনার অঞ্চলে উপলব্ধ নয়।'),
    (('unsupported url',),
     'এই লিংকটি সাপোর্ট করা হয় না।'),
    (('video unavailable', 'removed', 'does not exist', 'not found', '404'),
     'ভিডিওটি পাওয়া যায়নি বা মুছে ফেলা হয়েছে।'),
    (('sign in to confirm', 'bot'),
     'সাইটটি সার্ভারকে অস্থায়ীভাবে ব্লক করেছে। কিছুক্ষণ পরে আবার চেষ্টা করুন।'),
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
    logger.error(f"[{platform}] all strategies failed: {combined}")
    status = 404 if any(k in combined.lower() for k in ('not found', 'unavailable', 'does not exist')) else 500
    raise EngineError(friendly_error(combined), status=status)


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


# --- UNIVERSAL Video Downloader ---
DEFAULT_UA = DESKTOP_UA


def encode_headers(headers):
    return base64.urlsafe_b64encode(json.dumps(headers).encode('utf-8')).decode('ascii')


def decode_headers(value):
    try:
        data = json.loads(base64.urlsafe_b64decode(value.encode('ascii')).decode('utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_proxy_url(stream_url, headers=None):
    params = {'stream_url': stream_url}
    if headers:
        params['h'] = encode_headers(headers)
    return '/api/proxy_video?' + urlencode(params)


def direct_result(info, platform, page_url):
    title = info.get('title', 'Video')
    direct = pick_direct_format(info)
    if direct is None:
        return None
    proxied = build_proxy_url(direct['url'], direct.get('http_headers') or {})
    return {
        'success': True,
        'title': title,
        'thumbnail': info.get('thumbnail', ''),
        'duration': info.get('duration', 0),
        'uploader': info.get('uploader', 'Unknown'),
        'ext': direct.get('ext') or 'mp4',
        'platform': info.get('extractor', platform),
        'video_url': proxied,
        'url': proxied,
        'original_url': page_url,
        'filename': safe_filename(title),
    }


@app.route('/api/health')
def health():
    return jsonify({'ok': True, 'platforms': list_platforms(),
                    'cookies': bool(ensure_cookie_file())})


@app.route('/api/platforms')
def platforms():
    return jsonify({'platforms': list_platforms()})


@app.route('/api/fetch', methods=['POST'])
def fetch_video_data():
    data = request.get_json(silent=True) or {}
    text = (data.get('url') or '').strip()
    if not text:
        return jsonify({'success': False, 'error': 'লিংক বা কিউওয়ার্ড দিন'}), 400

    try:
        # ইউটিউব/কিউওয়ার্ড: আগে NewPipe/Piped
        if 'youtube.com' in text or 'youtu.be' in text or not is_url(text):
            np_result = fetch_via_newpipe(text)
            if np_result:
                return jsonify(np_result)

        is_search = not is_url(text)
        platform = detect_platform(text)
        page_url = text if is_search else normalize_url(text)
        target = f'ytsearch1:{text}' if is_search else page_url
        extra = {'noplaylist': False} if is_search else None

        info, strategy = extract_info(target, platform, download=False, extra_opts=extra)
        info = first_entry(info)
        page = info.get('webpage_url') or page_url

        result = direct_result(info, platform, page)
        if result:
            result['strategy'] = strategy
            return jsonify(result)

        # সরাসরি ফাইল নেই -> সার্ভারে ডাউনলোড জব
        jid = jobs.submit(page, platform)
        return jsonify({'success': True, 'pending': True, 'job_id': jid, 'status': 'queued',
                        'platform': platform, 'title': info.get('title', 'Video')}), 202

    except EngineError as e:
        return jsonify({'success': False, 'error': str(e)}), e.status
    except Exception as e:
        logger.exception('fetch failed')
        return jsonify({'success': False, 'error': friendly_error(str(e))}), 500


@app.route('/api/jobs/<jid>')
def job_status(jid):
    job = jobs.get(jid)
    if not job:
        return jsonify({'success': False, 'error': 'জবটি পাওয়া যায়নি বা মেয়াদ শেষ'}), 404
    body = {'success': job['status'] != 'error', 'status': job['status'], 'job_id': jid}
    if job['status'] == 'done':
        body.update(job['result'])
        body['success'] = True
    elif job['status'] == 'error':
        body['error'] = job['error']
    return jsonify(body)


@app.route('/api/proxy_video')
def proxy_video():
    stream_url = request.args.get('stream_url', '')
    if urlparse(stream_url).scheme not in ('http', 'https'):
        return "Invalid URL", 400

    headers = {'User-Agent': DEFAULT_UA, 'Accept': '*/*', 'Accept-Encoding': 'identity'}
    for k, v in decode_headers(request.args.get('h', '')).items():
        if isinstance(k, str) and isinstance(v, str):
            headers[k] = v
    if request.headers.get('Range'):
        headers['Range'] = request.headers['Range']

    try:
        r = requests.get(stream_url, headers=headers, stream=True, timeout=(10, 60), allow_redirects=True)
    except requests.RequestException as e:
        return f"Error streaming video: {str(e)[:100]}", 502

    if r.status_code not in (200, 206):
        r.close()
        return f"Upstream returned HTTP {r.status_code}", 502

    response_headers = {}
    for h in ['Content-Type', 'Content-Length', 'Accept-Ranges', 'Content-Range']:
        if r.headers.get(h):
            response_headers[h] = r.headers.get(h)
    response_headers.setdefault('Content-Type', 'video/mp4')

    def generate():
        try:
            for chunk in r.iter_content(chunk_size=512 * 1024):
                if chunk:
                    yield chunk
        finally:
            r.close()

    return Response(generate(), status=r.status_code, headers=response_headers)


@app.route('/api/proxy_thumb')
def proxy_thumb():
    thumb_url = request.args.get('url')
    if not thumb_url:
        return "", 404
    try:
        r = requests.get(thumb_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10, stream=True)
        return Response(r.iter_content(8192), content_type=r.headers.get('Content-Type','image/jpeg'))
    except:
        return "", 404

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"\n✅ PUBLIC SERVER: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
