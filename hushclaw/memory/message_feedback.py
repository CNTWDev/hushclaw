"""Explicit user feedback. No provider calls, autonomous endorsement or execution.

Current state and append-only revisions share one SQLite transaction. Message
ownership/visibility and scope are checked here, not trusted from the browser.
"""
from __future__ import annotations

import hashlib
import json
import time


class FeedbackError(ValueError):
    pass


def anchor_quote(content: str, quote: str) -> tuple[int, int]:
    """Anchor rendered selections across Markdown emphasis/whitespace only.

    Return the original raw slice. Never trust a client-supplied paraphrase as
    source evidence. Other rendering transformations fail closed with guidance.
    """
    if not isinstance(quote, str) or not 2 <= len(quote.strip()) <= 4000:
        raise FeedbackError('请选择 2–4000 字的观点片段。')
    start = content.find(quote)
    if start >= 0:
        return start, start + len(quote)
    def mapped(text):
        chars, offsets = [], []
        for i, char in enumerate(text):
            if char.isspace() or char in '*_`':
                continue
            chars.append(char)
            offsets.append(i)
        return ''.join(chars), offsets
    plain, positions = mapped(content)
    selected, _ = mapped(quote)
    start = plain.find(selected) if len(selected) >= 2 else -1
    if start < 0:
        raise FeedbackError('无法匹配原文，请重新选择较短片段，或从原始 Markdown 中复制。')
    return positions[start], positions[start + len(selected) - 1] + 1


class MessageFeedbackStore:
    def __init__(self, memory):
        self.memory, self.conn = memory, memory.conn

    def _source(self, mid, sid):
        source = self.memory.resolve_message_ref(mid, session_id=sid) if sid else None
        if not source or source.get('hidden') or source.get('excluded') or source['role'] != 'assistant':
            raise FeedbackError('回复不存在、已隐藏或不可评价。请刷新对话。')
        return source

    def _scope(self, sid, global_scope=False):
        if global_scope:
            return 'global'
        row = self.conn.execute('SELECT workspace FROM sessions WHERE session_id=?', (sid,)).fetchone()
        return 'workspace:' + row[0] if row and row[0] else 'session:' + sid

    @staticmethod
    def _hash(text):
        return hashlib.sha256(text.encode()).hexdigest()

    @staticmethod
    def _item(row):
        return {**dict(row), 'payload': json.loads(row['payload'])}

    def list(self, mid, sid):
        source = self._source(mid, sid)
        digest = self._hash(source['content'])
        items = [self._item(r) for r in self.conn.execute(
            'SELECT * FROM message_feedback WHERE message_id=? AND session_id=? ORDER BY created,feedback_id', (source['message_id'], sid))]
        for item in items:
            item['stale'] = item['source_hash'] != digest
        active = [i for i in items if i['active'] and not i['stale']]
        return {'items': items, 'counts': {
            'endorsed': sum(i['payload']['stance'] == 'endorsed' for i in active),
            'saved': sum(i['payload']['memory_kind'] != 'none' for i in active),
            'inspiring': sum(i['payload']['inspiring'] for i in active),
            'rating': next((i['payload']['rating'] for i in active if not i['quote']), 0),
        }}

    def save(self, mid, sid, data):
        source = self._source(mid, sid)
        mid = source['message_id']
        content = source['content']
        fid = str(data.get('feedback_id') or '')
        current = self.conn.execute('SELECT * FROM message_feedback WHERE feedback_id=?', (fid,)).fetchone() if fid else None
        if fid and (not current or current['message_id'] != mid or current['session_id'] != sid):
            raise FeedbackError('评价记录不属于当前回复。')
        if current:
            if current['source_hash'] != self._hash(content):
                raise FeedbackError('原始回复已变化，请重新选择片段。')
            start, end, quote = current['start_offset'], current['end_offset'], current['quote']
        elif data.get('quote'):
            start, end = anchor_quote(content, data['quote'])
            quote = content[start:end]
            if len(quote) > 5000:
                raise FeedbackError('片段过长，请缩小选择范围。')
        else:
            start, end, quote = 0, 0, ''
        fid = fid or 'feedback-' + self._hash(f'{mid}:{self._hash(content)}:{start}:{end}')[:32]
        current = current or self.conn.execute('SELECT * FROM message_feedback WHERE feedback_id=?', (fid,)).fetchone()
        if current and current['source_hash'] != self._hash(content):
            raise FeedbackError('原始回复已变化，请重新选择片段。')
        previous = json.loads(current['payload']) if current else {}
        payload = {**dict(stance='unreviewed', inspiring=False, memory_kind='none', summary='', reason='', conditions='', rating=0, reasons=[]), **previous}
        for key in payload:
            if key in data:
                payload[key] = data[key]
        if payload['stance'] not in {'unreviewed', 'endorsed', 'disputed'} or payload['memory_kind'] not in {'none', 'viewpoint', 'method', 'reference'}:
            raise FeedbackError('无效的评价类型。')
        if type(payload['rating']) is not int or not 0 <= payload['rating'] <= 5 or type(payload['inspiring']) is not bool:
            raise FeedbackError('评分应为 0–5 星。')
        for key, maximum in [('summary', 500), ('reason', 1000), ('conditions', 1000)]:
            if not isinstance(payload[key], str) or len(payload[key]) > maximum:
                raise FeedbackError('评价内容超过长度限制。')
            payload[key] = payload[key].strip()
        allowed = {'切中问题', '有新角度', '有据可查', '可操作', '缺乏依据', '过于冗长', '偏离问题'}
        if not isinstance(payload['reasons'], list) or len(payload['reasons']) > 7 or any(not isinstance(r, str) or r not in allowed for r in payload['reasons']):
            raise FeedbackError('无效的评分原因。')
        if quote:
            if payload['rating'] or payload['reasons']:
                raise FeedbackError('五星评分只评价整条回复的帮助程度。')
            if payload['memory_kind'] in {'viewpoint', 'method'} and payload['stance'] != 'endorsed':
                raise FeedbackError('记为我的观点或方法，需要明确认可。')
        elif payload['stance'] != 'unreviewed' or payload['memory_kind'] != 'none' or payload['inspiring']:
            raise FeedbackError('请先选择具体片段；不能将整条回复默认为已认可。')
        global_scope = data.get('global_scope', current['scope'] == 'global' if current else False)
        active = data.get('active', True)
        if type(global_scope) is not bool or type(active) is not bool:
            raise FeedbackError('无效的作用范围。')
        scope = self._scope(sid, global_scope)
        encoded = json.dumps(payload, ensure_ascii=False)
        if current and current['payload'] == encoded and current['scope'] == scope and current['active'] == int(active):
            return self.list(mid, sid)  # idempotent retries
        expected = data.get('revision', 0)
        if type(expected) is not int or expected != (current['revision'] if current else 0):
            raise FeedbackError('评价已更新，请重新打开后修改。')
        now = int(time.time())
        revision = expected + 1
        from hushclaw.memory.events import _conn_lock
        with _conn_lock(self.conn), self.conn:
            if current:
                changed = self.conn.execute('UPDATE message_feedback SET scope=?,payload=?,revision=?,active=?,updated=? WHERE feedback_id=? AND revision=?',
                    (scope, encoded, revision, int(active), now, fid, expected)).rowcount
            else:
                changed = self.conn.execute('INSERT OR IGNORE INTO message_feedback VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (fid, mid, sid, self._hash(content), quote, start, end, scope, encoded, revision, int(active), now, now)).rowcount
            if not changed:
                raise FeedbackError('评价已更新，请重新打开后修改。')
            self.conn.execute('INSERT INTO message_feedback_events(feedback_id,revision,payload,created) VALUES(?,?,?,?)',
                (fid, revision, json.dumps({'payload': payload, 'scope': scope, 'active': active}, ensure_ascii=False), now))
        self.memory._recall_cache.clear()
        return self.list(mid, sid)

    def context(self, query, sid):
        from hushclaw.memory.personalization import relevance
        scopes = ['global', self._scope(sid)]
        items = []
        sources = {}
        for row in self.conn.execute('SELECT * FROM message_feedback WHERE active=1 AND scope IN (?,?) ORDER BY updated DESC LIMIT 500', scopes):
            p = json.loads(row['payload'])
            if not row['quote'] or p['stance'] == 'disputed' or (p['memory_kind'] == 'none' and p['stance'] != 'endorsed'):
                continue
            key = (row['message_id'], row['session_id'])
            if key not in sources:
                sources[key] = self.memory.resolve_message_ref(key[0], session_id=key[1])
            source = sources[key]
            if not source or source.get('hidden') or source.get('excluded') or self._hash(source['content']) != row['source_hash']:
                continue
            text = p['summary'] or row['quote']
            score = relevance(query, text + ' ' + p['conditions'])
            if score < .12:
                continue
            accepted = p['stance'] == 'endorsed'
            items.append(dict(id=row['feedback_id'], kind='viewpoint' if accepted else 'reference',
                label={'method': '你认可的方法', 'viewpoint': '你认可的观点', 'reference': '你收藏的参考', 'none': '你认可的观点'}[p['memory_kind']],
                text=text, conditions=p['conditions'], reason=p['reason'], stance=p['stance'],
                origin='assistant', verification='confirmed' if accepted else 'saved_reference',
                feedback_revision=row['revision'],
                source_message_id=row['message_id'], source_session_id=row['session_id'],
                source_excerpt=row['quote'], updated=row['updated'], score=score + (.2 if p['memory_kind'] != 'none' else 0)))
        items.sort(key=lambda x: (x['score'], x['updated']), reverse=True)
        return items[:4]

    def forget(self, *, mid='', sid=''):
        field, value = ('message_id', mid) if mid else ('session_id', sid)
        self.conn.execute(f'DELETE FROM message_feedback_events WHERE feedback_id IN (SELECT feedback_id FROM message_feedback WHERE {field}=?)', (value,))
        self.conn.execute(f'DELETE FROM message_feedback WHERE {field}=?', (value,))

    def is_current(self, fid, revision):
        row = self.conn.execute('SELECT * FROM message_feedback WHERE feedback_id=?', (fid,)).fetchone()
        if not row or not row['active'] or row['revision'] != revision or json.loads(row['payload'])['stance'] == 'disputed':
            return False
        source = self.memory.resolve_message_ref(row['message_id'], session_id=row['session_id'])
        return bool(source and not source.get('hidden') and not source.get('excluded') and self._hash(source['content']) == row['source_hash'])
