# ১. NewPipe Java সার্ভিস build (Gradle wrapper + vendored NewPipe Extractor)
FROM docker.io/library/eclipse-temurin:17-jdk AS newpipe-build
WORKDIR /src
COPY newpipe-service/ ./
RUN chmod +x gradlew && ./gradlew --no-daemon installDist

# ২. পাইথন রানটাইম (yt-dlp 2026.x-এর জন্য Python 3.11+ লাগে)
FROM docker.io/library/python:3.11-slim

# ৩. সিস্টেম টুলস: ffmpeg (মার্জ), curl, git, Java runtime (NewPipe সার্ভিসের জন্য)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# ৪. ওয়ার্কিং ডিরেক্টরি
WORKDIR /app

# ৫. পাইথন ডিপেন্ডেন্সি
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ৬. NewPipe সার্ভিসের বিল্ট ফাইল
COPY --from=newpipe-build /src/build/install/newpipe-service /opt/newpipe

# ৭. প্রয়োজনীয় ফোল্ডার
RUN mkdir -p uploads static/downloads && touch database.json

# ৮. পুরো প্রজেক্ট কপি
COPY . .
RUN chmod +x start.sh

EXPOSE 10000

# ৯. Java সার্ভিস (127.0.0.1:8081) + পাইথন অ্যাপ একসাথে
CMD ["sh", "start.sh"]
