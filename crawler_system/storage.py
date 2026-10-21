"""Local material paths and atomic files; the shared index stays at its old path."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .errors import CrawlerError
from .regions import REGION_LABELS


def validate_storage_root(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise CrawlerError('STORAGE_PATH_INVALID', 'Please enter full path for storage directory', 'Enter an absolute path or choose a folder')
    path = Path(str(value).strip()).expanduser()
    if not path.is_absolute():
        raise CrawlerError('STORAGE_PATH_INVALID', 'Storage location must be an absolute path', 'e.g. D:\\Dev\\project\\crawler-materials')
    try:
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path) as file:
            file.write(b'crawler-storage-check')
            file.flush()
    except OSError as error:
        raise CrawlerError('STORAGE_NOT_WRITABLE', f'Storage directory not writable: {path}', 'Choose a writable directory and check disk availability') from error
    return path


def artifact_directory(root, region, category):
    if region not in REGION_LABELS or category not in {'资料', '下载', '导出'}:
        raise ValueError('Invalid artifact category or source region')
    base = Path(root).resolve()
    path = (base / REGION_LABELS[region] / category).resolve()
    if not path.is_relative_to(base):
        raise CrawlerError('STORAGE_PATH_INVALID', 'Material subdirectory points outside storage location', 'Choose a storage location without external links')
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_text(path, value):
    path = Path(path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.crawler-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    except OSError as error:
        raise CrawlerError('STORAGE_WRITE_FAILED', f'Cannot save material file: {path}', 'Check storage location, disk space, and permissions') from error
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def material_path(root, region, item_id):
    # IDs are generated locally, never derived from a site's title or URL.
    if not item_id or any(char not in '0123456789abcdef-' for char in item_id):
        raise ValueError('Invalid material ID')
    return artifact_directory(root, region, '资料') / (item_id + '.json')


def write_material(item, root):
    path = material_path(root, item['source_region'], item['id'])
    item['material_path'] = str(path)
    atomic_text(path, json.dumps(item, ensure_ascii=False, indent=2))


def choose_folder(initial=''):
    if os.name != 'nt':
        raise CrawlerError('FOLDER_PICKER_UNAVAILABLE', 'Windows folder picker unavailable on this system', 'Enter absolute path for storage directory directly')
    script = Path(__file__).parent.parent / 'build' / 'Choose-Folder.ps1'
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-InitialPath', str(initial)],
                                capture_output=True, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW, timeout=300)
    except subprocess.TimeoutExpired as error:
        raise CrawlerError('FOLDER_PICKER_TIMEOUT', 'Folder picker timed out', 'Choose folder again or enter path directly') from error
    if result.returncode != 0:
        raise CrawlerError('FOLDER_PICKER_FAILED', 'Failed to open folder picker window', 'Enter full storage path directly')
    return result.stdout.strip() or None
