"""Normal StaticFiles behavior after the patched ASGI dependency upgrade."""
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from demo.seed import ensure_demo_target


class DependencyRuntimeTests(unittest.TestCase):
    def test_static_asset_full_head_and_bounded_range(self):
        ensure_demo_target()
        from webapp.main import app

        asset = Path(__file__).resolve().parents[1] / 'static/js/login.js'
        expected = asset.read_bytes()
        with TestClient(app) as client:
            response = client.get('/static/js/login.js')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, expected)
            self.assertEqual(response.headers['cache-control'], 'no-store')

            head = client.head('/static/js/login.js')
            self.assertEqual(head.status_code, 200)
            self.assertEqual(head.content, b'')
            self.assertEqual(int(head.headers['content-length']), len(expected))

            # One ordinary bounded range, never a DoS payload.
            partial = client.get('/static/js/login.js', headers={'Range': 'bytes=0-15'})
            self.assertEqual(partial.status_code, 206)
            self.assertEqual(partial.content, expected[:16])
            self.assertEqual(partial.headers['content-range'], f'bytes 0-15/{len(expected)}')
            self.assertEqual(client.get('/static/demo-missing-asset.js').status_code, 404)


if __name__ == '__main__':
    unittest.main()
