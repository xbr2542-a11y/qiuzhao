"""本机转发小工具：绕开 github.com 的 DNS 污染。

背景
----
这台电脑所在的网络把 github.com 解析到了假 IP（205.251.193.165 这类
CloudFront 段地址），导致 git / 浏览器都连不上真站。而直连 GitHub 的真实
IP（140.82.112-116.x）是通的。

它做什么
--------
在本机 127.0.0.1 上开一个极小的 HTTP CONNECT 代理。git 把请求交给它，
它对 github.com 改用写死的真实 IP 建连，其余域名照常按 DNS 解析。

为什么这样是安全的
------------------
TLS 握手仍然是 git 和 GitHub 服务器之间端到端完成的，证书按 github.com
正常校验。本工具只搬运密文，看不到内容，也没有降低任何校验强度。
（对比之下，"改成 IP 连接 + 关掉证书校验"才是真的不安全，所以没那样做。）

用法
----
    python tools/github_net_proxy.py --port 18765       # 前台运行
    python tools/github_net_proxy.py --check            # 只测哪个 IP 可用
"""

from __future__ import annotations

import argparse
import socket
import ssl
import sys
import threading
import time

# GitHub 官方公布的 github.com 地址段（140.82.112.0/20）里的几个 IP。
# 挂着多个是为了某个 IP 不通时自动换下一个。
GITHUB_IPS = [
    "140.82.113.3", "140.82.112.3", "140.82.114.3", "140.82.116.4",
    "140.82.115.3", "140.82.121.3", "140.82.112.4", "140.82.113.4",
]

# 只有这些域名走"写死 IP"，其它一律正常解析，避免误伤。
PINNED_HOSTS = {"github.com": GITHUB_IPS, "gist.github.com": GITHUB_IPS}

# GitHub Pages 的地址段（和 github.com 不是同一批 IP，不能混用）。
# 这台网络的 DNS 污染同样影响 *.github.io，所以自己也搭的 Pages 站会打不开。
PAGES_IPS = ["185.199.108.153", "185.199.109.153", "185.199.110.153", "185.199.111.153"]
PINNED_SUFFIXES = {".github.io": PAGES_IPS}


def pinned_ips_for(host: str) -> list[str] | None:
    """这个域名该用哪组写死的 IP；不需要绕行的返回 None。"""
    if host in PINNED_HOSTS:
        return PINNED_HOSTS[host]
    for suffix, ips in PINNED_SUFFIXES.items():
        if host.endswith(suffix):
            return ips
    return None

PROBE_TIMEOUT = 3.0
BUF = 65536
CACHE_SECONDS = 120.0

_lock = threading.Lock()
# 每个域名各缓存一个已验证的 IP：github.com 和 *.github.io 用的是不同的 IP 段
_cache: dict[str, tuple[str, float]] = {}


def tls_ok(ip: str, host: str) -> bool:
    """真正做一次 TLS 握手来判断这个 IP 是否可用。

    为什么不用 TCP 探测：这台网络里有中间设备会对部分 IP "接受连接但重置
    TLS 握手"，只看 connect_ex 会挑到一个握不上手的 IP，表现就是 git 报
    "schannel: failed to receive handshake"。所以必须把握手做完，
    并且按 github.com 校验证书。
    """
    context = ssl.create_default_context()
    try:
        with socket.create_connection((ip, 443), timeout=PROBE_TIMEOUT) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return tls.version() is not None
    except (OSError, ssl.SSLError):
        return False


def pick_ip(host: str) -> str | None:
    """挑一个能完成 TLS 握手的 GitHub IP，结果缓存一会儿避免每次都探测。"""
    candidates = pinned_ips_for(host)
    if not candidates:
        return None
    with _lock:
        cached = _cache.get(host)
        if cached and (time.time() - cached[1]) < CACHE_SECONDS:
            return cached[0]
        for ip in candidates:
            if tls_ok(ip, host):
                _cache[host] = (ip, time.time())
                print(f"[proxy] 使用 GitHub IP {ip}", flush=True)
                return ip
        _cache.pop(host, None)
        return None


def invalidate_ip(host: str) -> None:
    """隧道出问题时清掉缓存，下一次连接重新探测。"""
    with _lock:
        _cache.pop(host, None)


def pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(BUF)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client: socket.socket) -> None:
    upstream: socket.socket | None = None
    try:
        client.settimeout(20)
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 8192:
            chunk = client.recv(1024)
            if not chunk:
                return
            head += chunk
        first = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = first.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return
        host, _, port_text = parts[1].partition(":")
        port = int(port_text or "443")

        target_ip = pick_ip(host)
        try:
            upstream = socket.create_connection((target_ip or host, port), timeout=15)
        except OSError:
            # 选中的 IP 连不上了：清缓存，换一个再试一次
            if target_ip:
                invalidate_ip(host)
                retry_ip = pick_ip(host)
                upstream = socket.create_connection(
                    (retry_ip or host, port), timeout=15)
            else:
                raise
        # 告诉 git：隧道建好了，后面是纯 TLS 字节流
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        client.settimeout(None)
        threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
        pipe(upstream, client)
    except OSError:
        pass
    finally:
        for sock in (client, upstream):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def serve(port: int) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(64)
    print(f"github 转发已启动：127.0.0.1:{port}（仅本机可连）", flush=True)
    while True:
        try:
            client, _ = server.accept()
        except OSError:
            break
        threading.Thread(target=handle, args=(client,), daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--check", action="store_true", help="只测试哪个 IP 可用后退出")
    args = parser.parse_args()

    if args.check:
        for ip in GITHUB_IPS:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(PROBE_TIMEOUT)
                ok = probe.connect_ex((ip, 443)) == 0
            print(f"  {ip:<16} {'可用' if ok else '不通'}")
        return 0

    serve(args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
