"""Create explicitly synthetic UI scenarios, never a release or an owner export."""
import copy
import hashlib
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
base = json.loads((root / 'tests/fixtures/student-package.json').read_text())
package = copy.deepcopy(base)
package['purpose'] = 'test'
package['export_id'] = hashlib.sha256(b'SYNTHETIC dashboard QA v1').hexdigest()
package['courses'] = []
package['groups'] = []
for p in package['programs']:
    p['course_permissions'] = []
physics = next(p for p in package['programs'] if p['bologna_program_id'] == 25)
large = '--large' in sys.argv
if large:
    package['export_id'] = hashlib.sha256(b'SYNTHETIC dashboard load QA v1').hexdigest()
for i in range(1, 301 if large else 9):
    c = copy.deepcopy(base['courses'][0])
    c.update(catalog_course_id=i, code=f'QA{i:03}', name=f'ÖRNEK Ders {i}')
    package['courses'].append(c)
    physics['course_permissions'].append({'catalog_course_id': i, 'status': 'eligible',
        'classifications': [{'category': 'compulsory' if i % 2 else 'professional_elective',
                             'year': (i-1) % 4 + 1, 'season': 'fall'}]})
    for j in range(4 if large else (2 if i <= 6 else 1)):
        g = copy.deepcopy(base['groups'][0])
        g.update(id=hashlib.sha256(f'QA{i}-{j}'.encode()).hexdigest(),catalog_course_id=i,
                 label=f'Gr.{j+1}',eligible_bologna_program_ids=[25])
        g['permissions']={'allowed_bologna_program_ids':[25], 'excluded_bologna_program_ids':[], 'unresolved':False}
        m = copy.deepcopy(base['groups'][0]['meetings'][0])
        hour = 9 + ((i-1)//2)*2
        m.update(weekday=1+j,starts_at=f'{hour:02}:00:00',ends_at=f'{hour+1:02}:00:00')
        if large:
            hour = 8 + ((i-1)//7*2) % 14
            m.update(weekday=(i-1)%7+1, starts_at=f'{hour:02}:00:00', ends_at=f'{hour+1:02}:00:00')
        g['meetings']=[m]
        if i==7 and not large:
            g.update(time_status='tba',meetings=[])
        if i==8 and not large:
            m.update(weekday=7, starts_at='22:00:00', ends_at='23:00:00')
        package['groups'].append(g)
target = root/'public/qa-student-preview.json' if large else root/'tests/fixtures/dashboard-qa.json'
target.write_text(json.dumps(package,ensure_ascii=False,indent=2)+'\n')
