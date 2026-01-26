#!/usr/bin/env python3
import http.server
import ssl
import os
import sys

# Change to frontend directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

handler = http.server.SimpleHTTPRequestHandler

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # Only log errors
        if args[0] != 200:
            print(f"[{args[0]}] {args[1] if len(args) > 1 else ''}")

try:
    # Setup HTTPS server
    httpd = http.server.HTTPServer(('0.0.0.0', 9000), QuietHandler)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    
    # Certificates in parent directory
    cert_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'cert.pem')
    key_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'key.pem')
    
    print(f"🔒 Loading SSL certificates from:\n  cert: {cert_path}\n  key: {key_path}")
    context.load_cert_chain(cert_path, key_path)
    
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    print("✅ HTTPS Server running on https://0.0.0.0:9000")
    httpd.serve_forever()
except Exception as e:
    print(f"❌ Error: {e}", file=sys.stderr)
    sys.exit(1)
