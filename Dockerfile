# ১. পাইথন Slim বেস ইমেজ ব্যবহার
FROM docker.io/library/python:3.11-slim

# ২. সিস্টেমের প্রয়োজনীয় টুলস ইনস্টল করা (ffmpeg, curl এবং git)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# ৩. ওয়ার্কিং ডিরেক্টরি সেট করা
WORKDIR /app

# ৪. ডিপেন্ডেন্সি ফাইল কন্টেইনারে কপি করা
COPY requirements.txt .

# ৫. পাইথন প্যাকেজগুলো ইনস্টল করা (গিট থাকায় এখন সফলভাবে বিল্ড হবে)
RUN pip install --no-cache-dir -r requirements.txt

# ৬. প্রয়োজনীয় ফোল্ডার ও ফাইল তৈরি করা
RUN mkdir -p uploads && touch database.json && touch cookies.txt

# ৭. পুরো প্রজেক্টের কোড কন্টেইনারে কপি করা
COPY . .

# ৮. পোর্ট এক্সপোজ করা
EXPOSE 10000

# ৯. অ্যাপ্লিকেশন চালু করার কমান্ড
CMD ["python", "app.py"]
