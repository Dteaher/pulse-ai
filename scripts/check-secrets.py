"""Scan reachable Git history and tracked working files; never print credential values.
Known-format detection is a preventative check, not a complete secret detector.
"""
import re
import subprocess
from pathlib import Path

PATTERN = re.compile(rb"(?<![A-Za-z0-9_-])(?:sk-(?:proj-|or-v1-)?[A-Za-z0-9_-]{28,}|gsk_[A-Za-z0-9]{30,}|AIza[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|glpat-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")

def main():
    root = Path(__file__).resolve().parents[1]
    objects = {}
    for line in subprocess.check_output(['git', 'rev-list', '--objects', '--all'], cwd=root).decode().splitlines():
        oid, _, name = line.partition(' ')
        objects.setdefault(oid, name)
    findings = set()
    count = 0
    batch = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    for oid, name in objects.items():
        batch.stdin.write((oid + '\n').encode()); batch.stdin.flush()
        header = batch.stdout.readline().decode().split()
        data = batch.stdout.read(int(header[2])); batch.stdout.read(1)
        if header[1] == 'blob' and b'\0' not in data[:8192]:
            count += 1
            if PATTERN.search(data): findings.add(name or oid)
    batch.stdin.close(); batch.wait()
    for name in subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0'):
        path = root / name
        if name and path.is_file():
            data = path.read_bytes()
            if b'\0' not in data[:8192] and PATTERN.search(data): findings.add(name)
    for name in sorted(findings): print('Potential credential in:', name)
    print(f'Scanned {count} historical text blobs; findings: {len(findings)}')
    return bool(findings)

if __name__ == '__main__': raise SystemExit(main())
