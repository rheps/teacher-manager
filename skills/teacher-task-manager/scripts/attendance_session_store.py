"""Windows-user protected attendance sessions; never stores Google tokens."""
import base64
import json
from pathlib import Path
from brity_bridge import component_lock


class AttendanceSessionStore:
    def __init__(self, config_dir, protect=None, unprotect=None):
        from dashboard.central_chat import _dpapi_protect, _dpapi_unprotect
        self.path = component_lock.prepare_direct_file_path(Path(config_dir) / '.attendance-session.protected')
        self.protect = protect or _dpapi_protect
        self.unprotect = unprotect or _dpapi_unprotect

    def load(self):
        if not self.path.exists():
            return None
        if self.path.stat().st_size > 32768:
            raise ValueError('Protected session invalid')
        return json.loads(self.unprotect(base64.b64decode(self.path.read_bytes(), validate=True)))

    def save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        protected = self.protect(json.dumps(value, separators=(',', ':')).encode('utf-8'))
        component_lock.atomic_write_text_unique(self.path, base64.b64encode(protected).decode('ascii'))

    def clear(self):
        self.path.unlink(missing_ok=True)
