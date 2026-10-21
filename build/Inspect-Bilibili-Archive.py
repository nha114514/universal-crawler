"""Re-extract previously inventoried ASAR members as exact bytes into a NEW folder."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--asar', default='C:/Program Files/bilibili/resources/app.asar')
    parser.add_argument('--old', default='D:/Dev/project/bilibili-video-reconstruction/evidence/client_extracted')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    archive, original, destination = Path(args.asar), Path(args.old), Path(args.output).resolve()
    if not Path(args.output).is_absolute() or destination.exists():
        raise ValueError('Output must be a new absolute directory; old evidence is preserved')
    names = ['package.json', *[path.relative_to(original).as_posix() for path in original.rglob('*') if path.is_file()]]
    records = []
    with archive.open('rb') as file:
        _, header_size, _, json_size = struct.unpack('<IIII', file.read(16))
        header = json.loads(file.read(json_size))
        # The outer size pickle is 8 bytes. JSON length excludes pickle padding.
        data_start = 8 + header_size
        destination.mkdir(parents=True)
        for name in dict.fromkeys(names):
            entry = header
            for component in name.split('/'):
                entry = entry['files'][component]
            if entry.get('unpacked') or 'offset' not in entry:
                raise ValueError('Member is not in the packed ASAR: ' + name)
            start, size = data_start + int(entry['offset']), int(entry['size'])
            if start < data_start or start + size > archive.stat().st_size:
                raise ValueError('Invalid member bounds: ' + name)
            target = (destination / name).resolve()
            if not target.is_relative_to(destination):
                raise ValueError('Unsafe member path')
            file.seek(start)
            data = file.read(size)
            if len(data) != size:
                raise ValueError('Truncated member: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            old = original / name
            before = old.read_bytes() if old.is_file() else None
            sha = hashlib.sha256(data).hexdigest()
            records.append({'member': name, 'bytes': size, 'sha256': sha, 'file_offset': start,
                            'old_exists': before is not None, 'old_matches': before == data,
                            'old_shifted_three_bytes': before is not None and len(before) == len(data) and before[3:] == data[:-3],
                            'new_matches': hashlib.sha256(target.read_bytes()).hexdigest() == sha})
    report = {'archive': str(archive.resolve()), 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'old_directory': str(original), 'verified_directory': str(destination), 'records': records}
    (destination / 'evidence-index.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'members': len(records), 'old_mismatches': sum(row['old_exists'] and not row['old_matches'] for row in records),
                      'new_mismatches': sum(not row['new_matches'] for row in records), 'output': str(destination)}))


if __name__ == '__main__':
    main()
