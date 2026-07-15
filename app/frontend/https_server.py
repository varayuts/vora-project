#!/usr/bin/env python3
"""
HTTPS Server for VORA Frontend
Allows microphone access on non-localhost connections
"""
import http.server
import ssl
import os
import sys

PORT = 9443
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# Change to frontend directory
os.chdir(DIRECTORY)

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # Only log errors
        if '404' in str(args) or '500' in str(args):
            print(f"[ERROR] {args}")

try:
    # SSL certificates in same directory
    cert_path = os.path.join(DIRECTORY, 'cert.pem')
    key_path = os.path.join(DIRECTORY, 'key.pem')
    
    if not os.path.exists(cert_path) or not os.path.exists(key_path):
        print("❌ SSL certificates not found!")
        print(f"   Looking for: {cert_path}")
        sys.exit(1)
    
    # Setup HTTPS server
    httpd = http.server.HTTPServer(('0.0.0.0', PORT), QuietHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    
    print(f"🔒 HTTPS Server running on port {PORT}")
    print(f"")
    print(f"📱 Access from mobile:")
    print(f"   https://100.102.217.45:{PORT}")
    print(f"   https://192.168.104.31:{PORT}")
    print(f"")
    print(f"⚠️  Browser will show 'Not Secure' warning")
    print(f"   → Click 'Advanced' → 'Proceed anyway'")
    print(f"🎤 Microphone will work after accepting!")
    
    httpd.serve_forever()
except Exception as e:
    print(f"❌ Error: {e}", file=sys.stderr)
    sys.exit(1)


