#!/bin/sh
# NewPipe Java সার্ভিস ব্যাকগ্রাউন্ডে চালায়, তারপর Flask অ্যাপ।
# Java সার্ভিস না চললেও অ্যাপ চলবে (yt-dlp fallback)।
if [ -x /opt/newpipe/bin/newpipe-service ]; then
  NEWPIPE_PORT="${NEWPIPE_PORT:-8081}" JAVA_OPTS="${JAVA_OPTS:--Xmx192m}" \
    /opt/newpipe/bin/newpipe-service &
else
  echo "NewPipe service not found, continuing with yt-dlp only"
fi
exec python app.py
