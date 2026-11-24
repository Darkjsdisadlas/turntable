#!/usr/bin/env python3
"""Stardust Turntable 로컬 서버.

같은 폴더의 music/ 안에 있는 음원을 목록으로 만들어 주고 재생용으로 내보냅니다.
  python3 serve.py                 → http://localhost:8000
  python3 serve.py --port 9000     → 포트 변경
  python3 serve.py --music ~/Music → 다른 음악 폴더 사용
  python3 serve.py --host 0.0.0.0  → 같은 와이파이의 폰에서도 접속
"""
import argparse, json, mimetypes, os, re, sys, threading, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, quote

ROOT = os.path.dirname(os.path.abspath(__file__))
AUDIO = {'.mp3', '.m4a', '.aac', '.wav', '.flac', '.ogg', '.oga', '.opus', '.webm', '.weba'}
LYRICS = ('.lrc', '.srt', '.vtt')
IMAGES = ('.jpg', '.jpeg', '.png', '.webp')
COVER_NAMES = ('cover', 'folder', 'front', 'album')
MIME = {
    '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.aac': 'audio/aac', '.wav': 'audio/wav',
    '.flac': 'audio/flac', '.ogg': 'audio/ogg', '.oga': 'audio/ogg', '.opus': 'audio/ogg',
    '.webm': 'audio/webm', '.weba': 'audio/webm',
    '.lrc': 'text/plain; charset=utf-8', '.srt': 'text/plain; charset=utf-8', '.vtt': 'text/vtt; charset=utf-8',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp',
}
MUSIC = os.path.join(ROOT, 'music')


def natural(s):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', s)]


def scan():
    tracks = []
    for dirpath, dirnames, files in os.walk(MUSIC):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        by_stem = {}
        dir_cover = None
        for f in files:
            stem, ext = os.path.splitext(f)
            ext = ext.lower()
            if ext in LYRICS or ext in IMAGES:
                by_stem.setdefault(stem.lower(), {})[ext] = f
            if ext in IMAGES and stem.lower() in COVER_NAMES and dir_cover is None:
                dir_cover = f
        rel_dir = os.path.relpath(dirpath, MUSIC)
        rel_dir = '' if rel_dir == '.' else rel_dir.replace(os.sep, '/') + '/'
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() not in AUDIO or f.startswith('.'):
                continue
            side = by_stem.get(stem.lower(), {})
            lrc = next((side[e] for e in LYRICS if e in side), None)
            img = next((side[e] for e in IMAGES if e in side), None) or dir_cover
            tracks.append({
                'path': rel_dir + f,
                'lrc': rel_dir + lrc if lrc else None,
                'cover': rel_dir + img if img else None,
            })
    tracks.sort(key=lambda t: natural(t['path']))
    return tracks


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'StardustTurntable'

    def log_message(self, fmt, *args):
        pass

    def _send_bytes(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _send_file(self, full):
        try:
            size = os.path.getsize(full)
            f = open(full, 'rb')
        except OSError:
            return self._send_bytes(404, b'not found', 'text/plain')
        ext = os.path.splitext(full)[1].lower()
        ctype = MIME.get(ext) or mimetypes.guess_type(full)[0] or 'application/octet-stream'
        start, end, code = 0, size - 1, 200
        rng = self.headers.get('Range')
        if rng:
            m = re.match(r'bytes=(\d*)-(\d*)$', rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                if start >= size:
                    f.close()
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                end = min(end, size - 1)
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            if self.command != 'HEAD':
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            f.close()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ('/', '/index.html'):
            return self._send_file(os.path.join(ROOT, 'index.html'))
        if path == '/favicon.ico':
            return self._send_bytes(204, b'', 'image/x-icon')
        if path == '/api/tracks':
            body = json.dumps({'tracks': scan()}, ensure_ascii=False).encode('utf-8')
            return self._send_bytes(200, body, 'application/json; charset=utf-8')
        if path.startswith('/music/'):
            base = os.path.realpath(MUSIC)
            full = os.path.realpath(os.path.join(base, *path[len('/music/'):].split('/')))
            if full != base and not full.startswith(base + os.sep):
                return self._send_bytes(403, b'forbidden', 'text/plain')
            if os.path.isfile(full):
                return self._send_file(full)
        self._send_bytes(404, b'not found', 'text/plain')


def main():
    global MUSIC
    ap = argparse.ArgumentParser(description='Stardust Turntable 서버')
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--music', default=MUSIC, help='음원 폴더 (기본: music/)')
    ap.add_argument('--no-open', action='store_true', help='브라우저를 자동으로 열지 않음')
    a = ap.parse_args()
    MUSIC = os.path.abspath(os.path.expanduser(a.music))
    os.makedirs(MUSIC, exist_ok=True)
    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as e:
        sys.exit(f'포트 {a.port}를 열 수 없어요: {e}\n다른 포트로 실행해 보세요: python3 serve.py --port {a.port + 1}')
    url = f'http://localhost:{a.port}'
    print(f'음악 폴더: {MUSIC}')
    print(f'발견한 곡: {len(scan())}곡')
    print(f'열기: {url}   (종료: Ctrl+C)')
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n종료합니다.')


if __name__ == '__main__':
    main()
