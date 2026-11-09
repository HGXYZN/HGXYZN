from pathlib import Path, PureWindowsPath[cite: 7]
from urllib.parse import unquote, urlparse[cite: 7]
import ctypes[cite: 7]
import uuid[cite: 7]
import re[cite: 7]
import sys[cite: 7]

FOLDERS = {
    'desktop': 'B4BFCC3A-DB2C-424C-B029-7FE99A87C641',
    'downloads': '374DE290-123F-4565-9164-39C4925E467B',
    'documents': 'FDD39AD0-238F-46AF-ADB4-6C85480369C7',
    'pictures': '33E28130-4E1E-4676-835A-98395C3BC3BB',
    'music': '4BD8D571-6D19-48D3-BE97-422220080E43',
    'videos': '18989B1D-99B5-455B-841C-AB7C74E4DDFC',
}[cite: 7]

def known_folders():
    result = {}
    for key, guid in FOLDERS.items():
        pointer = ctypes.c_void_p()
        ident = ctypes.create_string_buffer(uuid.UUID(guid).bytes_le)
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(ident), 0x4000, 0, ctypes.byref(pointer)) == 0:
            try:
                result[key] = Path(ctypes.wstring_at(pointer))
            finally:
                ctypes.windll.ole32.CoTaskMemFree(pointer)
    result.setdefault('downloads', Path.home() / 'Downloads')
    return result[cite: 7]

def valid_part(name):
    return (
        isinstance(name, str)
        and len(name) > 0
        and name not in ('.', '..')
        and name.rstrip('. ') == name
        and not re.search(r'[\x00-\x1f<>:"/\\|?*]', name)
        and not PureWindowsPath(name).is_reserved()
    )[cite: 7]

def delivery_location(value):
    if value is None:
        return ''
    parts = value.split('/') if isinstance(value, str) and len(value) <= 400 else []
    if parts and parts[0] in FOLDERS and len(parts) <= 7 and all(valid_part(p) for p in parts[1:]):
        return '/'.join(parts)
    return ''[cite: 7]

def source_location(info, folders):
    name = info.get('name', '')
    if not valid_part(name):
        return ''

    def matches(path):
        try:
            st = path.stat()
            return st.st_size == info.get('size') and abs(st.st_mtime_ns // 1_000_000 - info.get('modified', -100000)) <= 2000
        except (OSError, ValueError, TypeError):
            return False

    raw = info.get('uri') or ''
    if raw.startswith('file:///'):
        url = urlparse(raw)
        if not url.netloc:
            path = Path(unquote(url.path).lstrip('/'))
            if path.is_absolute() and path.name.casefold() == name.casefold():
                return classify(path, folders) or ''

    folder = info.get('explorer')
    if folder and Path(folder).is_absolute():
        path = Path(folder) / name
        if matches(path):
            return classify(path, folders) or ''

    candidates = [key for key, base in folders.items() if matches(base / name)]
    if len(candidates) == 1:
        return candidates[0]
    return ''[cite: 7]

def classify(path, folders):
    for key, folder in sorted(folders.items(), key=lambda v: -len(v[1].parts)):
        if path.parent.is_relative_to(folder):
            return delivery_location('/'.join([key, *path.parent.relative_to(folder).parts]))
    return ''[cite: 7]

def destination_dir(location, folders):
    parts = (delivery_location(location) or 'downloads').split('/')
    folder = folders.get(parts[0])
    if folder:
        dest = folder.joinpath(*parts[1:])
        if dest.is_dir() and dest.resolve().is_relative_to(folder.resolve()):
            return dest
    target = folders['downloads']
    target.mkdir(parents=True, exist_ok=True)
    return target[cite: 7]

def safe_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name)).strip().rstrip('. ')
    if not name:
        return 'File ricevuto'
    if not valid_part(name) or name.lower() in ('desktop.ini', 'autorun.inf'):
        name = '_' + name
    return name[:220].rstrip('. ') or 'File ricevuto'[cite: 7]