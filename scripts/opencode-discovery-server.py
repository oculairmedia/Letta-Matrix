#!/usr/bin/env python3
"""
Simple HTTP server that returns running OpenCode instances.
Listens on port 3202 (localhost only).
"""

import subprocess
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import os

class DiscoveryHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress logging
        pass
    
    def do_GET(self):
        if self.path == '/discover':
            instances = self.discover_opencode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(instances).encode())
        elif self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()
    
    def discover_opencode(self):
        """Find all running OpenCode instances with their directories and ports."""
        instances = []
        
        try:
            # Find OpenCode PIDs
            result = subprocess.run(['pgrep', '-f', '^opencode(\\s|$)'], 
                                  capture_output=True, text=True)
            pids = result.stdout.strip().split('\n') if result.stdout.strip() else []
            
            for pid in pids:
                if not pid:
                    continue
                    
                # Get working directory
                try:
                    cwd = os.readlink(f'/proc/{pid}/cwd')
                except:
                    continue
                
                # Get listening port using ss
                try:
                    ss_result = subprocess.run(
                        ['ss', '-tlnp'],
                        capture_output=True, text=True
                    )
                    for line in ss_result.stdout.split('\n'):
                        if f'pid={pid},' in line:
                            # Extract port from 127.0.0.1:PORT
                            import re
                            match = re.search(r'127\.0\.0\.1:(\d+)', line)
                            if match:
                                port = int(match.group(1))
                                instances.append({
                                    'pid': int(pid),
                                    'directory': cwd,
                                    'port': port,
                                    'hostname': '127.0.0.1'
                                })
                                break
                except:
                    pass
                    
        except Exception as e:
            print(f"Discovery error: {e}")
        
        return instances

if __name__ == '__main__':
    port = 3202
    server = HTTPServer(('127.0.0.1', port), DiscoveryHandler)
    print(f'OpenCode Discovery Server running on http://127.0.0.1:{port}')
    print(f'Endpoints: /discover, /health')
    server.serve_forever()
