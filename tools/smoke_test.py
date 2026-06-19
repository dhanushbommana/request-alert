import sys
import time
import requests

HOST = 'http://127.0.0.1:5000'

def main(iterations=10, pause=0.2):
    iterations = int(iterations)
    for i in range(iterations):
        print(f'[{i+1}/{iterations}] GET / ->', end=' ')
        try:
            r = requests.get(HOST + '/')
            print(r.status_code)
        except Exception as e:
            print('ERR', e)
        print(' GET /alerts/unread_count ->', end=' ')
        try:
            r = requests.get(HOST + '/alerts/unread_count')
            print(r.status_code, r.text)
        except Exception as e:
            print('ERR', e)
        # optional: test-email (may spam your inbox) — run only if MAIL_USERNAME set
        if i % 10 == 0:
            print(' GET /test-email ->', end=' ')
            try:
                r = requests.get(HOST + '/test-email', timeout=30)
                print(r.status_code, r.text[:200])
            except Exception as e:
                print('ERR', e)
        time.sleep(pause)

if __name__ == '__main__':
    it = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    pause = float(sys.argv[2]) if len(sys.argv) > 2 else 0.2
    main(it, pause)
