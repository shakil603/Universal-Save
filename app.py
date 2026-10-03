import os
import json
import logging
import time
import datetime
import requests
from flask import Flask, request, jsonify, render_template, Response, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import yt_dlp

app = Flask(__name__, template_folder='.', static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'universal_save_super_secret_key')
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- PUBLIC LIFETIME PERSISTENCE CONFIG ---
# Render এ static/uploads ফোল্ডার পাবলিক, data.json সারা জীবন সেভ
DATA_FILE = os.environ.get('DATA_FILE', 'marketplace_data.json')
UPLOAD_FOLDER = os.path.join('static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

# ফোল্ডার তৈরি - না থাকলে বানাবে
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs('uploads', exist_ok=True)

def load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # ensure keys
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
    # GitHub এ ফাইল নাম Index-1.html / index_1.html / index.html যে কোনো একটা হতে পারে
    for fname in ['index_1.html', 'Index-1.html', 'index.html', 'Index_1.html']:
        if os.path.exists(fname):
            return render_template(fname)
    return "index_1.html not found", 404

@app.route('/static/uploads/<path:filename>')
def uploaded_files(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/uploads/<path:filename>')
def uploads_compat(filename):
    # পুরোনো লিংক /uploads/ সাপোর্ট
    if os.path.exists(os.path.join('uploads', filename)):
        return send_from_directory('uploads', filename)
    return send_from_directory(UPLOAD_FOLDER, filename)

# --- PUBLIC API - সবাই এখান থেকে দেখবে ---
@app.route('/api/data', methods=['GET'])
def get_data():
    db = load_db()
    # No cache so public sees instantly
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
            "img": f"/{image_path}",  # PUBLIC LINK - সবাই দেখবে
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "createdAt": datetime.datetime.now().isoformat()
        }
        # নতুনটা সবার উপরে - public এ সবাই প্রথমে দেখবে
        db['products'].insert(0, new_product)
        save_db(db)
        logger.info(f"[PUBLIC SAVE] {new_product['name']} -> {new_product['img']}")

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

# --- UNIVERSAL Video Downloader - Fixed for All Platforms ---
@app.route('/api/fetch', methods=['POST'])
def fetch_video_data():
    data = request.get_json() or {}
    url_or_keyword = data.get('url', '').strip()
    if not url_or_keyword:
        return jsonify({'error': 'লিংক বা কিউওয়ার্ড দিন'}), 400

    # Universal yt-dlp options for all platforms
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'nocheckcertificate': True,
        'noplaylist': True,
        'ignoreerrors': True,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'no_check_certificate': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'skip': ['hls', 'dash']
            },
            'facebook': {
                'facebook_version': 'web'
            },
            'tiktok': {
                'api_hostname': 'api16-normal-c-useast2a.tiktokv.com'
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
        },
        'socket_timeout': 30,
    }

    is_search = not url_or_keyword.startswith(('http://', 'https://'))
    if is_search:
        url_or_keyword = f"ytsearch1:{url_or_keyword}"
        ydl_opts['noplaylist'] = False

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url_or_keyword, download=False)

            # Handle search results
            if 'entries' in info:
                entries = [e for e in info['entries'] if e]
                if not entries:
                    return jsonify({'error': 'কোনো ভিডিও পাওয়া যায়নি'}), 404
                video_data = entries[0]
            else:
                video_data = info

            # Try to get best URL
            raw_video_url = None
            direct_url = video_data.get('url')
            formats = video_data.get('formats', [])

            # Priority 1: Find mp4 with both audio and video
            if formats:
                # Sort by quality
                for f in reversed(formats):
                    url = f.get('url', '')
                    if not url: continue
                    if 'manifest' in url or 'm3u8' in url: continue
                    vcodec = f.get('vcodec', 'none')
                    acodec = f.get('acodec', 'none')
                    ext = f.get('ext', '')
                    # Prefer mp4 with both
                    if vcodec != 'none' and acodec != 'none' and ext == 'mp4':
                        raw_video_url = url
                        break
                # Priority 2: Any with both
                if not raw_video_url:
                    for f in reversed(formats):
                        url = f.get('url', '')
                        if not url: continue
                        if 'manifest' in url: continue
                        if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                            raw_video_url = url
                            break
                # Priority 3: Best video
                if not raw_video_url:
                    for f in reversed(formats):
                        url = f.get('url', '')
                        if url and 'manifest' not in url:
                            raw_video_url = url
                            break

            # Fallback to direct url
            if not raw_video_url and direct_url and 'http' in direct_url:
                raw_video_url = direct_url

            if not raw_video_url:
                # Last try: best format
                best = formats[-1].get('url') if formats else None
                if best:
                    raw_video_url = best

            if not raw_video_url:
                return jsonify({'error': 'ভিডিও লিংক পাওয়া যায়নি। লিংকটি সঠিক কিনা চেক করুন।'}), 404

            # Proxy the URL to avoid CORS / expiry issues
            proxied_video_url = f"/api/proxy_video?stream_url={requests.utils.quote(raw_video_url)}"

            title = video_data.get('title') or video_data.get('fulltitle') or 'Video'
            # Clean filename
            safe_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()[:80]

            return jsonify({
                'success': True,
                'title': title,
                'thumbnail': video_data.get('thumbnail', '') or video_data.get('thumbnails', [{}])[-1].get('url','') if video_data.get('thumbnails') else '',
                'duration': video_data.get('duration', 0),
                'uploader': video_data.get('uploader') or video_data.get('channel') or 'Unknown',
                'ext': video_data.get('ext', 'mp4'),
                'platform': video_data.get('extractor', 'unknown'),
                'video_url': proxied_video_url,
                'url': proxied_video_url,
                'original_url': raw_video_url,
                'filename': f"{safe_title}.mp4"
            })

    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e)
        logger.error(f"yt-dlp DownloadError: {err_msg}")
        if 'Private' in err_msg or 'private' in err_msg:
            return jsonify({'error': 'এটি প্রাইভেট ভিডিও, ডাউনলোড করা যাবে না'}), 403
        elif 'not available' in err_msg.lower():
            return jsonify({'error': 'ভিডিওটি এই দেশে পাওয়া যাচ্ছে না'}), 403
        elif 'Unsupported URL' in err_msg:
            return jsonify({'error': 'এই লিংক সাপোর্ট করে না। YouTube, TikTok, Facebook, Instagram, Twitter লিংক দিন'}), 400
        else:
            return jsonify({'error': f"ডাউনলোড ব্যর্থ: {err_msg[:200]}"}), 500
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return jsonify({'error': f"ব্যর্থ হয়েছে। কারণ: {str(e)[:200]}"}), 500

@app.route('/api/proxy_video')
def proxy_video():
    stream_url = request.args.get('stream_url')
    if not stream_url:
        return "Missing URL", 400
    try:
        # Universal headers for all platforms
        req_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'identity',
            'Referer': 'https://www.youtube.com/',
            'Origin': 'https://www.youtube.com',
            'Range': request.headers.get('Range', ''),
            'Sec-Fetch-Mode': 'cors',
        }
        # For TikTok / FB / Insta need different referer
        if 'tiktok' in stream_url:
            req_headers['Referer'] = 'https://www.tiktok.com/'
        elif 'fbcdn' in stream_url or 'facebook' in stream_url:
            req_headers['Referer'] = 'https://www.facebook.com/'

        r = requests.get(stream_url, headers=req_headers, stream=True, timeout=30, verify=False)
        
        # Filter headers to pass through
        response_headers = {}
        for h in ['Content-Type', 'Content-Length', 'Accept-Ranges', 'Content-Range', 'Content-Disposition']:
            if r.headers.get(h):
                response_headers[h] = r.headers.get(h)
        
        # Force mp4 if not set
        if 'Content-Type' not in response_headers:
            response_headers['Content-Type'] = 'video/mp4'
        
        # Add CORS for player
        response_headers['Access-Control-Allow-Origin'] = '*'
        response_headers['Access-Control-Allow-Headers'] = 'Range'
        response_headers['Access-Control-Expose-Headers'] = 'Content-Range, Content-Length'

        def generate():
            try:
                for chunk in r.iter_content(chunk_size=512*1024):
                    if chunk:
                        yield chunk
            except Exception as e:
                logger.error(f"Stream error: {e}")

        status = r.status_code if r.status_code in [200, 206] else 200
        return Response(generate(), status=status, headers=response_headers)
    except Exception as e:
        logger.error(f"Proxy error: {e}")
        return f"Error streaming video: {str(e)[:100]}", 500

@app.route('/api/proxy_thumb')
def proxy_thumb():
    # Thumbnail proxy for Instagram/FB
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
    print(f"📂 Uploads: {UPLOAD_FOLDER} - PUBLIC")
    print(f"💾 DB: {DATA_FILE} - Lifetime\n")
    app.run(host='0.0.0.0', port=port, debug=False)
