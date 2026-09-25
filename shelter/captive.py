"""接続時にページを自動で開く（キャプティブポータル）。

スマホは Wi-Fi に入った直後に決まった URL（iPhone は captive.apple.com、Android は
connectivitycheck.gstatic.com など）へ「ネットにつながるか」を確かめに行く。ここで
避難所 PC が答えを横取りして入口ページへ飛ばすと、端末が「このネットワークにサインイン」
の画面を自動で出し、避難所ページが表示される。ポスターは Wi-Fi の QR 1枚で済む。

仕組み（Windows モバイルホットスポット＝ICS で確認・2026-09-25）:
- ICS の DNS 中継は 0.0.0.0:53 で待つ。こちらが **ホットスポットの IP（192.168.137.1）:53**
  に bind すると、そちらが優先されて避難者の名前解決はここに来る。ICS の設定変更は不要
- DNS はすべての名前を PC の IP と答える（ネット無し前提。AAAA は「無し」と答えて IPv4 に寄せる）
- HTTP は PC の IP:80 で待ち、どの URL も入口ページ（:8000）へ 302 で飛ばす
- どちらも管理者権限は要らない。ファイアウォールで UDP 53・TCP 80 の受信を許可する

ホットスポットが OFF などで bind できないときは警告だけ出して普通に動く。

**既定は OFF（2026-09-25・v1.5）**: iPhone で接続確認の横取りまでは届いたが、自動で開くかは機種・設定
（Android は通知だけ・モバイルデータ優先・プライベート DNS）で揃わず、サインイン画面は Safari とは保存領域が
別になりうる（自己登録の端末 ID・クッキーが別人扱いになるおそれ）。入口ポスターは常に Wi-Fi と URL の QR 2枚。
試すときだけ .env に SHELTER_CAPTIVE=1。
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import config

log = logging.getLogger("shelter.captive")

_lock = threading.Lock()
_dns_sock: socket.socket | None = None
_http: ThreadingHTTPServer | None = None
_threads: list[threading.Thread] = []
_state: dict = {"enabled": config.captive, "active": False, "host_ip": "", "entry_url": "",
                "error": ""}

# 参考: 各 OS の接続確認先（DNS は全部同じ答えを返すので一覧は説明用）
CHECK_HOSTS = ("captive.apple.com", "connectivitycheck.gstatic.com", "connectivitycheck.android.com",
               "clients3.google.com", "www.msftconnecttest.com", "www.msftncsi.com")


# --- DNS -------------------------------------------------------------------
def _dns_answer(query: bytes, ip: str) -> bytes | None:
    """A の問い合わせは ip を、AAAA など他は「答え無し」を返す（形式が変なら None）。"""
    if len(query) < 17:
        return None
    i = 12
    while query[i] != 0:  # 質問名を読み飛ばす
        i += query[i] + 1
        if i >= len(query):
            return None
    qtype = struct.unpack(">H", query[i + 1:i + 3])[0]
    qend = i + 5
    if qend > len(query):
        return None
    head = query[:2] + b"\x81\x80"  # 応答・再帰あり・NOERROR
    if qtype != 1:
        return head + struct.pack(">HHHH", 1, 0, 0, 0) + query[12:qend]
    return (head + struct.pack(">HHHH", 1, 1, 0, 0) + query[12:qend]
            + b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 30, 4) + socket.inet_aton(ip))


def _dns_loop(sock: socket.socket, ip: str) -> None:
    while True:
        try:
            data, addr = sock.recvfrom(512)
        except OSError:
            return  # stop() で閉じた
        try:
            resp = _dns_answer(data, ip)
            if resp:
                sock.sendto(resp, addr)
        except Exception as e:  # noqa: BLE001
            log.debug("dns: %s", e)


# --- HTTP ------------------------------------------------------------------
def _make_handler(entry_url: str):
    class Handler(BaseHTTPRequestHandler):
        def _redirect(self) -> None:
            self.send_response(302)
            self.send_header("Location", entry_url)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = do_HEAD = _redirect

        def log_message(self, *args) -> None:  # noqa: D401
            log.debug("http %s %s%s", self.client_address[0], self.headers.get("Host", ""), self.path)

    return Handler


# --- 起動・停止 ----------------------------------------------------------------
def start(host_ip: str, entry_url: str) -> dict:
    """host_ip:53（DNS）と host_ip:80（HTTP）で待ち始める。失敗しても例外は出さない。"""
    global _dns_sock, _http
    with _lock:
        _stop_locked()
        _state.update(host_ip=host_ip, entry_url=entry_url, active=False, error="")
        if not config.captive:
            _state["error"] = "SHELTER_CAPTIVE=0 で無効"
            return status()
        try:
            d = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            d.bind((host_ip, 53))
            h = ThreadingHTTPServer((host_ip, 80), _make_handler(entry_url))
            h.daemon_threads = True
        except OSError as e:
            _state["error"] = f"{host_ip} の 53/80 番で待ち受けできません（ホットスポット OFF か IP 違い）: {e}"
            log.warning("接続時の自動表示は無効: %s", _state["error"])
            return status()
        _dns_sock, _http = d, h
        t1 = threading.Thread(target=_dns_loop, args=(d, host_ip), daemon=True, name="captive-dns")
        t2 = threading.Thread(target=h.serve_forever, daemon=True, name="captive-http")
        t1.start()
        t2.start()
        _threads[:] = [t1, t2]
        _state["active"] = True
        log.info("接続時の自動表示: DNS %s:53 / HTTP %s:80 → %s", host_ip, host_ip, entry_url)
        return status()


def _stop_locked() -> None:
    global _dns_sock, _http
    if _http is not None:
        _http.shutdown()
        _http.server_close()
        _http = None
    if _dns_sock is not None:
        _dns_sock.close()
        _dns_sock = None
    _threads.clear()
    _state["active"] = False


def stop() -> None:
    with _lock:
        _stop_locked()


def status() -> dict:
    return dict(_state)
