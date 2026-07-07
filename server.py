import http.server
import socketserver
import mimetypes
import sys

PORT = 8778

# Explicitly register MIME types to bypass any corrupt Windows Registry configurations
mimetypes.add_type('text/html', '.html')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('image/png', '.png')
mimetypes.add_type('image/svg+xml', '.svg')

class PremiumHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Force UTF-8 charset headers for HTML, CSS, and JS to prevent character decoding errors
        ctype = self.guess_type(self.translate_path(self.path))
        if ctype == 'text/html':
            self.send_header('Content-Type', 'text/html; charset=utf-8')
        elif ctype == 'text/css':
            self.send_header('Content-Type', 'text/css; charset=utf-8')
        elif ctype == 'text/javascript':
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
        super().end_headers()

# Allow port reuse to avoid 'Address already in use' errors on quick restarts
socketserver.TCPServer.allow_reuse_address = True

if __name__ == '__main__':
    try:
        with socketserver.TCPServer(("", PORT), PremiumHTTPRequestHandler) as httpd:
            print(f"Serving La Liga Dashboard on http://localhost:{PORT}")
            print("Press Ctrl+C to stop.")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
        sys.exit(0)
