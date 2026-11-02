"""Check the staged/tracked publication boundary without reading local owner data."""
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
paths = [path for path in paths if path]
if not paths:
    sys.exit('No indexed files. Stage the public source before running this check.')
forbidden_prefixes = (
    'api/data/', 'web/public/data/', 'web/private-tests/',
    'web/node_modules/', 'local-owner/node_modules/', 'api/.venv/',
    'web/.next/', 'local-owner/.next/', 'web/out/',
)
forbidden_names = {'HANDOVER.md', 'BUILD_PROGRESS.md', 'docs/FOUR_DEPARTMENT_PILOT.md',
                   'web/scripts/pilot-preview.mts'}
public_markdown = {'README.md', 'CONTRIBUTING.md', 'api/README.md',
                   'web/README.md', 'local-owner/README.md'}
forbidden_suffixes = {'.db', '.sqlite', '.sqlite3', '.pdf', '.xlsx', '.xls', '.xlsm',
                      '.csv', '.zip', '.pem', '.key', '.log'}
credential = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}|\bAKIA[A-Z0-9]{16}\b|\bsk-proj-[A-Za-z0-9_-]{30,}')
errors = []
for path in paths:
    p = Path(path)
    blocked = (path.startswith(forbidden_prefixes) or path in forbidden_names
               or (p.suffix.lower() == '.md' and path not in public_markdown)
               or p.name in {'.Rhistory', '.RData', '.Ruserdata'}
               or p.suffix.lower() in forbidden_suffixes
               or (p.name.startswith('.env') and p.name not in {'.env.example', '.env.local.example'})
               or (path.startswith('web/public/') and p.suffix == '.json')
               or (path.startswith('docs/qa/') and path != 'docs/qa/README.md')
               or any(part in {'node_modules', '.next', '.venv', '__pycache__', '.pytest_cache', '.ruff_cache'} for part in p.parts))
    if blocked:
        errors.append(f'Excluded data/generated path is indexed: {path}')
        continue
    content = subprocess.check_output(['git', 'show', f':{path}'], cwd=root)
    if credential.search(content):
        errors.append(f'Possible credential in index: {path}')
if errors:
    sys.exit('\n'.join(errors))
print(f'Public index check passed: {len(paths)} files; no forbidden data paths or credential patterns.')
