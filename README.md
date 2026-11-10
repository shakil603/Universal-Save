# Universal Save

একটি ওপেন-সোর্স ভিডিও ডাউনলোডার (Flask + yt-dlp + ffmpeg)।

## চালানো
```bash
pip install -r requirements.txt
python app.py            # http://localhost:10000
python -m unittest discover -s tests -v
```
Docker: `docker build -t universal-save . && docker run -p 10000:10000 universal-save`

## Engine (`app.py` — ডাউনলোড ইঞ্জিন)
- প্ল্যাটফর্ম ডিটেকশন (YouTube, Facebook, Instagram, TikTok, X/Twitter, Vimeo, Reddit ইত্যাদি)
- একাধিক yt-dlp strategy ক্রমান্বয়ে চেষ্টা (desktop/mobile UA, YouTube client fallback)
- ফাইল মার্জ/HLS/DASH এর জন্য ব্যাকগ্রাউন্ড জব (`/api/jobs/<id>`)
- বাংলায় ব্যাখ্যামূলক এরর (লগইন, প্রাইভেট, geo, বট-ব্লক ইত্যাদি)

## Cookies (Facebook / Instagram / প্রাইভেট কনটেন্টের জন্য)
Facebook অনেক রিল/ভিডিওতে লগইন ছাড়া ডাউনলোড করতে দেয় না। নিজের ব্রাউজার থেকে Netscape-format
`cookies.txt` এক্সপোর্ট করে তা base64 করে env var হিসেবে সেট করুন:

```bash
base64 -w0 cookies.txt        # আউটপুটটি YTDLP_COOKIES_B64 হিসেবে Render/Docker-এ দিন
```
`cookies.txt` কখনো Git-এ commit করবেন না (`.gitignore`-এ আছে)।

## API
| Method | Path | বিবরণ |
|---|---|---|
| POST | `/api/fetch` | `{url}` — সরাসরি লিংক (200) অথবা জব (202, `job_id`) |
| GET | `/api/jobs/<id>` | জবের স্ট্যাটাস: `queued` / `running` / `done` / `error` |
| GET | `/api/proxy_video?stream_url=` | ভিডিও স্ট্রিম প্রক্সি |
| GET | `/api/health` | স্বাস্থ্য ও সাপোর্টেড প্ল্যাটফর্ম |
| GET | `/api/platforms` | সাপোর্টেড প্ল্যাটফর্মের তালিকা |
