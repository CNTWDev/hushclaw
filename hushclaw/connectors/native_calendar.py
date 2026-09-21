"""Opt-in macOS calendar source. EventKit stays outside the harness kernel.

Only explicit authorization invokes a system prompt. Background refresh uses
selected calendars, stages a bounded snapshot, then replaces only its own rows.
Notes, attendees and conferencing secrets are deliberately not imported.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from hushclaw.config.schema import CalendarConfig
from hushclaw.connectors.caldav_sync import CalDAVSyncService

_build_lock = threading.Lock()


class NativeCalendarReader:
    def __init__(self, data_dir):
        self.root = Path(data_dir) / 'helpers' / 'HushClawCalendar.app'
        self.binary = self.root / 'Contents/MacOS/HushClawCalendar'

    def build(self):
        if sys.platform != 'darwin':
            raise RuntimeError('本机日历目前仅支持运行在 macOS 上的 HushClaw 服务。')
        source = Path(__file__).parent / 'native/CalendarReader.swift'
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        stamp = self.root / 'Contents/Resources/source.sha256'
        with _build_lock:
            if self.binary.exists() and stamp.exists() and stamp.read_text() == digest:
                return
            compiler = subprocess.run(['/usr/bin/xcrun', '--find', 'swiftc'], capture_output=True, text=True)
            if compiler.returncode:
                raise RuntimeError('请先安装 Apple Command Line Tools（xcode-select --install），再重试本机日历。')
            self.root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(prefix='calendar-build-', dir=self.root.parent) as temp:
                app = Path(temp) / 'HushClawCalendar.app'; content = app / 'Contents'
                (content / 'MacOS').mkdir(parents=True)
                (content / 'Resources').mkdir()
                info = {'CFBundleExecutable':'HushClawCalendar', 'CFBundleIdentifier':'com.hushclaw.calendar-reader',
                        'CFBundleName':'HushClaw Calendar', 'CFBundlePackageType':'APPL', 'CFBundleVersion':'1',
                        'LSUIElement':True, 'NSCalendarsUsageDescription':'读取你选中的日历，在 HushClaw 聚合展示行程。不修改系统日历。',
                        'NSCalendarsFullAccessUsageDescription':'读取你选中的日历，在 HushClaw 聚合展示行程。不修改系统日历。'}
                (content / 'Info.plist').write_bytes(plistlib.dumps(info))
                result = subprocess.run(['/usr/bin/xcrun', '--sdk', 'macosx', 'swiftc', str(source), '-O', '-o', str(content / 'MacOS/HushClawCalendar'),
                    '-framework', 'EventKit', '-framework', 'AppKit', '-module-cache-path', str(Path(temp) / 'modules')], capture_output=True, text=True, timeout=120)
                if result.returncode:
                    raise RuntimeError('本机日历辅助程序编译失败，请更新 Apple Command Line Tools。')
                (content / 'Resources/source.sha256').write_text(digest)
                subprocess.run(['/usr/bin/codesign','--force','--sign','-','--identifier','com.hushclaw.calendar-reader',str(app)],
                               capture_output=True, check=True, timeout=20)
                # Replace the executable only after the new bundle is complete.
                self.root.mkdir(exist_ok=True)
                shutil.copytree(content, self.root / 'Contents', dirs_exist_ok=True)

    def call(self, command, payload=None):
        if command not in {'status','authorize','calendars','read'}:
            raise ValueError('Unsupported native calendar operation')
        if not self.binary.exists():
            if command == 'status':
                return {'permission':'not_installed'}
            raise RuntimeError('请先在「日历来源」中启用本机日历辅助程序。')
        try:
            result = subprocess.run([str(self.binary), command], input=json.dumps(payload or {}),
                capture_output=True, text=True, timeout=100 if command == 'authorize' else 30)
            data = json.loads(result.stdout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('本机日历读取超时；已保留上次快照。') from exc
        except (ValueError, OSError) as exc:
            raise RuntimeError('本机日历辅助程序未正常响应。') from exc
        if result.returncode or data.get('error'):
            errors = {'calendar_permission_required':'尚未获得日历读取权限，请在系统设置 → 隐私与安全性 → 日历中授权。',
                      'selected_calendar_unavailable':'选中的日历已不可用，请重新选择来源；上次快照已保留。'}
            raise RuntimeError(errors.get(data.get('error'), '本机日历读取失败；上次快照已保留。'))
        return data


class NativeCalendarSyncService(CalDAVSyncService):
    def __init__(self, memory):
        self.reader = NativeCalendarReader(memory.data_dir)
        self._selection_lock = asyncio.Lock()
        super().__init__(CalendarConfig(enabled=True, url='native://macos-calendar', username='local', sync_interval_minutes=5), memory)

    def preferences(self):
        row = self._memory.conn.execute("SELECT config_json FROM calendar_sources WHERE source_id='macos'").fetchone()
        return json.loads(row[0]) if row else {'enabled':False, 'calendar_ids':[]}

    def _ready_to_sync(self):
        return bool(self.preferences()['enabled'])

    async def start(self):
        if self._ready_to_sync():
            await super().start()

    async def status(self, authorize=False):
        if authorize:
            await asyncio.to_thread(self.reader.build)
        result = await asyncio.to_thread(self.reader.call, 'authorize' if authorize else 'status')
        if result['permission'] == 'authorized':
            result.update(await asyncio.to_thread(self.reader.call, 'calendars'))
        return {**result, **self.preferences(), 'last_sync':self.last_sync, 'last_error':self.last_error,
                'supported':True}

    async def select(self, enabled, calendar_ids):
        if type(enabled) is not bool or not isinstance(calendar_ids, list) or len(calendar_ids)>100 or any(not isinstance(i,str) or len(i)>500 for i in calendar_ids):
            raise ValueError('无效的本机日历来源。')
        async with self._selection_lock:
            if enabled:
                catalog = await asyncio.to_thread(self.reader.call, 'calendars')
                allowed = {c['id'] for c in catalog['calendars']}
                if not calendar_ids or not set(calendar_ids) <= allowed:
                    raise ValueError('请至少选择一个当前可访问的日历。')
            await self.stop()
            self._memory.conn.execute("INSERT INTO calendar_sources(source_id,config_json) VALUES('macos',?) ON CONFLICT(source_id) DO UPDATE SET config_json=excluded.config_json",
                (json.dumps({'enabled':enabled,'calendar_ids':list(dict.fromkeys(calendar_ids))}),))
            self._memory.conn.commit()
            if enabled:
                self._stop_event.clear()
                await self.sync()
                await self.start()
            else:
                self._memory.clear_synced_calendar_source('macos', self._sync_key)
                self._last_sync = 0.0
                self._last_error = ''
            return await self.status()

    def _fetch_and_upsert(self, cfg):
        with self._sync_lock:
            prefs = self.preferences()
            if self._stop_event.is_set() or not prefs['enabled']:
                return 0
            now = time.time()
            data = self.reader.call('read', {'from':now-90*86400, 'to':now+275*86400, 'calendar_ids':prefs['calendar_ids']})
            rows = []
            if not isinstance(data.get('events'), list) or len(data['events']) > 20000:
                raise ValueError('无效的本机日历快照；上次数据已保留。')
            for e in data['events']:
                # Validate the entire snapshot before replacing any cached rows.
                parse = date.fromisoformat if e['all_day'] else datetime.fromisoformat
                start, end = parse(e['start_time']), parse(e['end_time'])
                if (end <= start or e['calendar_id'] not in prefs['calendar_ids']
                        or not e['uid'] or not isinstance(e['title'], str)
                        or (not e['all_day'] and (start.tzinfo is None or end.tzinfo is None))):
                    raise ValueError('无效的本机日历行程；上次数据已保留。')
                identity = json.dumps([e['calendar_id'],e['uid'],e['start_time']])
                rows.append({'event_id':'macos:'+hashlib.sha256(identity.encode()).hexdigest(),
                    'title':e['title'], 'start_time':e['start_time'], 'end_time':e['end_time'],
                    'all_day':e['all_day'], 'location':e['location'], 'remote_uid':e['uid'],
                    'remote_calendar':e['calendar_id'], 'remote_etag':e['calendar_title']})
            if self._stop_event.is_set():
                raise RuntimeError('本机日历刷新已停止；上次快照已保留。')
            return self._memory.replace_calendar_source_events('macos', rows)


if __name__ == '__main__':
    from hushclaw.paths import get_data_dir
    NativeCalendarReader(get_data_dir()).build()
    print('Calendar helper ready; permission has not been requested.')
