"""Serve the browser tester without automatically opening the microphone."""
import argparse
from pathlib import Path

from wakeword_runtime.runtime import TestRuntimeHttpServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8006)
    args = parser.parse_args()
    server = TestRuntimeHttpServer(Path(__file__).resolve().parent, host='127.0.0.1', port=args.port)
    print(f'Browser tester: {server.page_url}', flush=True)
    print('Use the recording button on the page to enable microphone input.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
