
import sys
from pathlib import Path
from types import SimpleNamespace
import importlib.util
from flask import Flask

root = Path(__file__).resolve().parents[1]
for extra in [root, root / 'Lib' / 'site-packages']:
    if extra.exists() and str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

module_path = root / 'app' / 'tools' / 'cloudshift.py'
spec = importlib.util.spec_from_file_location('cloudshift_preview_module', module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

app = Flask(
    __name__,
    instance_path=str(root / 'var' / 'cloudshift-preview-instance'),
    template_folder=str(root / 'app' / 'templates'),
    static_folder=str(root / 'app' / 'static')
)
app.secret_key = 'preview'
app.config['TESTING'] = False
app.config['LOGIN_DISABLED'] = True
app.register_blueprint(module.cloudshift_bp)
module.current_user = SimpleNamespace(is_authenticated=True, username='owner01', name='Owner User')

with app.test_client() as client:
    create = client.post('/tools/shiftersync/cloudshift/api/create', data={
        'title': 'Mobile Preview',
        'mode': 'scene',
        'year': '2026',
        'month': '8',
        'required_capacity': '2',
    })
    payload = create.get_json()['project']
    project_id = payload['project']['id']
    month = payload['month']
    entries = dict(month['entries_per_day'])
    entries['1'] = [
        {'id': 'a1', 'value': '!A!?? ??', 'comment': '????????'},
        {'id': 'a2', 'value': '????', 'comment': '??????'},
    ]
    entries['2'] = [
        {'id': 'b1', 'value': '!M!?? ??', 'comment': '??????'},
    ]
    entries['4'] = [
        {'id': 'c1', 'value': '????', 'comment': '????'},
        {'id': 'c2', 'value': '!E!?? ??', 'comment': ''},
    ]
    entries['7'] = [
        {'id': 'd1', 'value': '!N1!?????', 'comment': '21???'},
    ]
    client.put(
        f'/tools/shiftersync/cloudshift/api/project/{project_id}/month/2026/8',
        json={
            'required_capacity': 2,
            'entries_per_day': entries,
            'base_month': month,
        },
    )
    view_url = payload['project']['urls']['view_url']
    (root / 'var' / 'cloudshift_preview_url.txt').write_text('http://127.0.0.1:5055' + view_url, encoding='utf-8')

app.run(host='127.0.0.1', port=5055, debug=False, use_reloader=False)
