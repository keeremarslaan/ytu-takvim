import { copyFileSync, existsSync, mkdirSync, unlinkSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Development-only invented fixture. Never copies owner data or enables production tests.
const root = fileURLToPath(new URL('../', import.meta.url));
const target = new URL('../public/demo-student-package.json', import.meta.url);
if (existsSync(target)) throw new Error('A demo file already exists. Remove it before restarting the demo.');
mkdirSync(new URL('../public/', import.meta.url), { recursive: true });
copyFileSync(new URL('../tests/fixtures/dashboard-qa.json', import.meta.url), target);
const child = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'dev', '--hostname', '127.0.0.1', ...process.argv.slice(2)], {
  cwd: root, stdio: 'inherit', env: { ...process.env, NEXT_PUBLIC_STUDENT_PACKAGE_URL: '/demo-student-package.json', NEXT_PUBLIC_ALLOW_TEST_PACKAGE: 'true', NEXT_PUBLIC_ALLOW_WEEKLY_PREVIEW: 'false' },
});
const cleanup = () => { if (existsSync(target)) unlinkSync(target); };
process.on('exit', cleanup);
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('error', error => { console.error(error.message); process.exitCode = 1; cleanup(); });
child.on('exit', (code, signal) => { cleanup(); process.exitCode = code ?? (signal === 'SIGINT' ? 0 : 1); });
