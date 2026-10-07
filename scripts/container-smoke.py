"""Exercise the actual built frontend/proxy/backend without paid API calls."""
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

def request(path, data=None, code=None):
    headers = {'Content-Type': 'application/json'}
    if code: headers['Authorization'] = 'Bearer ' + code
    req = Request('http://localhost' + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        with urlopen(req, timeout=15) as r: return r.status, r.read()
    except HTTPError as e: return e.code, e.read()

assert request('/')[0] == 200
status, body = request('/api/health')
assert status == 200 and json.loads(body)['access_required']
assert request('/api/process/generate', {})[0] == 401
assert request('/api/access/check', code='wrong')[0] == 401
code = 'ci-only-mock-workspace-code-not-a-real-secret'
assert request('/api/access/check', code=code)[0] == 200
_, examples = request('/api/examples')
status, body = request('/api/process/generate', {'text': json.loads(examples)[0]['text']}, code)
result = json.loads(body)
assert status == 200 and result['xml'] and result['process']['nodes']
assert result['metadata']['provider_used'] == 'mock'
assert code not in body.decode()
print('Container smoke passed: web, proxy, access, mock generation, BPMN XML')
