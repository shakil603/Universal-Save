import os
import json
import base64
import uuid
import logging
import time
import datetime
import requests
from flask import Flask, request, jsonify, render_template, Response, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
from urllib.parse import urlencode, urlparse
import yt_dlp

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

# --- UNIVERSAL Video Downloader ---
DEFAULT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
DOWNLOAD_DIR = os.path.join('static', 'downloads')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
DOWNLOAD_TTL = 60 * 60  # সার্ভারে তৈরি ফাইল ১ ঘণ্টা পরে মুছে যাবে
COOKIE_FILE = 'cookies.txt'


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


def safe_filename(title):
    name = "".join(c for c in (title or '') if c.isalnum() or c in (' ', '-', '_')).strip()[:80]
    return (name or 'video') + '.mp4'


def _is_http(fmt):
    return (fmt.get('protocol') or 'https') in ('http', 'https') and bool(fmt.get('url'))


def _has_audio_and_video(fmt):
    return fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none'


def pick_direct_format(info):
    """একটি ফাইলে ভিডিও+অডিও থাকা সরাসরি (HTTP) লিংক খুঁজে বের করে।"""
    if _is_http(info) and _has_audio_and_video(info):
        return info
    candidates = [f for f in (info.get('formats') or []) if _is_http(f) and _has_audio_and_video(f)]
    if not candidates:
        return None
    candidates.sort(key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))
    return candidates[-1]


def cleanup_downloads():
    now = time.time()
    try:
        for name in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, name)
            if os.path.isfile(path) and now - os.path.getmtime(path) > DOWNLOAD_TTL:
                os.remove(path)
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")


def base_ydl_opts():
    opts = {
        'nocheckcertificate': True,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'socket_timeout': 30,
        'retries': 3,
    }
    if os.path.exists(COOKIE_FILE) and os.path.getsize(COOKIE_FILE) > 0:
        opts['cookiefile'] = COOKIE_FILE
    return opts


def server_side_download(page_url, title):
    """HLS/DASH বা ভিডিও+অডিও আলাদা থাকলে yt-dlp (ffmpeg) দিয়ে সার্ভারে ডাউনলোড করে ফাইল দেয়।"""
    cleanup_downloads()
    job = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    opts = base_ydl_opts()
    opts.update({
        'format': 'bestvideo*+bestaudio/best',
        'merge_output_format': 'mp4',
        'outtmpl': os.path.join(DOWNLOAD_DIR, job + '.%(ext)s'),
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(page_url, download=True)
    if not info:
        return None
    files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(job + '.')]
    if not files:
        return None
    name = files[0]
    return {
        'success': True,
        'title': info.get('title', title),
        'thumbnail': info.get('thumbnail', ''),
        'duration': info.get('duration', 0),
        'uploader': info.get('uploader', 'Unknown'),
        'ext': os.path.splitext(name)[1].lstrip('.') or 'mp4',
        'platform': info.get('extractor', 'unknown'),
        'video_url': f"/static/downloads/{name}",
        'url': f"/static/downloads/{name}",
        'original_url': page_url,
        'filename': safe_filename(info.get('title', title)),
    }


@app.route('/api/fetch', methods=['POST'])
def fetch_video_data():
    data = request.get_json(silent=True) or {}
    url_or_keyword = (data.get('url') or '').strip()
    if not url_or_keyword:
        return jsonify({'error': 'লিংক বা কিউওয়ার্ড দিন'}), 400

    # ইউটিউব বা ইউটিউব সার্চের ক্ষেত্রে NewPipe/Piped ব্যাকএন্ড আগে চেষ্টা করবে
    if 'youtube.com' in url_or_keyword or 'youtu.be' in url_or_keyword or not url_or_keyword.startswith(('http://', 'https://')):
        np_result = fetch_via_newpipe(url_or_keyword)
        if np_result:
            return jsonify(np_result)

    is_search = not url_or_keyword.startswith(('http://', 'https://'))
    target = f"ytsearch1:{url_or_keyword}" if is_search else url_or_keyword

    opts = base_ydl_opts()
    opts.update({
        # একক ফাইলে ভিডিও+অডিও থাকা ফরম্যাট আগে নেওয়া হবে, যাতে সরাসরি লিংক পাওয়া যায়
        'format': 'best[vcodec!=none][acodec!=none][ext=mp4]/best[vcodec!=none][acodec!=none]/best',
        'noplaylist': not is_search,
    })

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
            if not info:
                return jsonify({'error': 'ভিডিও ইনফো পাওয়া যায়নি'}), 404
            if 'entries' in info:
                entries = [e for e in info['entries'] if e]
                if not entries:
                    return jsonify({'error': 'কোনো ভিডিও পাওয়া যায়নি'}), 404
                video_data = entries[0]
            else:
                video_data = info

        title = video_data.get('title', 'Video')
        page_url = video_data.get('webpage_url') or url_or_keyword
        direct = pick_direct_format(video_data)

        if direct is None:
            # সরাসরি ফাইল লিংক না থাকলে (HLS/DASH/আলাদা অডিও-ভিডিও) সার্ভারে ডাউনলোড করে দেওয়া হবে
            result = server_side_download(page_url, title)
            if not result:
                return jsonify({'error': 'ভিডিও ডাউনলোড করা যায়নি'}), 404
            return jsonify(result)

        proxied = build_proxy_url(direct['url'], direct.get('http_headers') or {})
        return jsonify({
            'success': True,
            'title': title,
            'thumbnail': video_data.get('thumbnail', ''),
            'duration': video_data.get('duration', 0),
            'uploader': video_data.get('uploader', 'Unknown'),
            'ext': direct.get('ext') or 'mp4',
            'platform': video_data.get('extractor', 'unknown'),
            'video_url': proxied,
            'url': proxied,
            'original_url': page_url,
            'filename': safe_filename(title),
        })
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp error: {e}")
        return jsonify({'error': f"ব্যর্থ হয়েছে: {str(e)[:200]}"}), 500
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return jsonify({'error': f"ব্যর্থ হয়েছে: {str(e)[:200]}"}), 500


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
