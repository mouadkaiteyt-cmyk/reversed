import urllib.request
import time
import datetime
import threading
import os

URL = os.environ.get('RENDER_EXTERNAL_URL', 'http://localhost:8000')
INTERVAL = 40  # 40 seconds

def ping():
    while True:
        try:
            response = urllib.request.urlopen(URL)
            status_code = response.getcode()
            print(f"[{datetime.datetime.now().isoformat()}] Pinged {URL} - Status: {status_code}")
        except Exception as e:
            print(f"[{datetime.datetime.now().isoformat()}] Error pinging {URL}: {e}")
        
        time.sleep(INTERVAL)

if __name__ == '__main__':
    print(f"Starting autoping for {URL} every {INTERVAL} seconds...")
    ping()
