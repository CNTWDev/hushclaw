import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hushclaw.memory.store import MemoryStore
from hushclaw.memory.message_feedback import FeedbackError, anchor_quote
from hushclaw.learning.controller import LearningController
from hushclaw.learning.reflection import TaskTrace
from hushclaw.providers.base import LLMResponse
from hushclaw.server import HushClawServer


@pytest.fixture
def memory(tmp_path):
    store = MemoryStore(tmp_path)
    yield store
    store.close()


def answer(memory, sid='s', workspace='', text='先验证付费意愿，再投入完整产品建设。'):
    memory.save_turn(sid, 'user', '讨论新业务', workspace=workspace)
    eid = memory.session_log.append(sid, 'assistant_message_emitted', {'text': text})
    return 'event:' + eid


def save(memory, mid, **kwargs):
    return memory.message_feedback.save(mid, 's', {'quote':'先验证付费意愿', **kwargs})['items'][0]


def test_quote_anchor_handles_markdown_but_rejects_paraphrases():
    raw = '应该**先验证**付费意愿，再投入。'
    a, b = anchor_quote(raw, '先验证付费意愿')
    assert raw[a:b] == '先验证**付费意愿'
    with pytest.raises(FeedbackError):
        anchor_quote(raw, '立即投入完整建设')


def test_inspiration_is_not_endorsement_or_personal_memory(memory):
    mid = answer(memory)
    item = save(memory, mid, inspiring=True)
    assert item['payload']['stance'] == 'unreviewed'
    assert memory.message_feedback.context('付费意愿', 's') == []
    assert memory.message_feedback.list(mid, 's')['counts'] == {'endorsed':0,'saved':0,'inspiring':1,'rating':0}


def test_saved_reference_is_not_endorsed(memory):
    mid = answer(memory)
    save(memory, mid, memory_kind='reference')
    entry = memory.message_feedback.context('付费意愿', 's')[0]
    assert entry['verification'] == 'saved_reference' and entry['kind'] == 'reference'


def test_viewpoint_requires_explicit_endorsement(memory):
    mid = answer(memory)
    with pytest.raises(FeedbackError):
        save(memory, mid, memory_kind='viewpoint')
    assert memory.conn.execute('SELECT count(*) FROM message_feedback').fetchone()[0] == 0
    item = save(memory, mid, stance='endorsed', memory_kind='viewpoint', conditions='仅限需求不确定的新业务', reason='控制试错成本')
    bundle = memory.personalization.context('新业务的付费意愿', 's')
    entry = next(e for e in bundle['evidence'] if e['id'] == item['feedback_id'])
    assert entry['verification'] == 'confirmed' and entry['origin'] == 'assistant'
    assert entry['conditions'] == '仅限需求不确定的新业务'
    assert 'permission to act' in memory.personalization.render(bundle)


def test_entire_answer_rating_never_becomes_belief(memory):
    mid = answer(memory)
    result = memory.message_feedback.save(mid, 's', {'rating':5, 'reasons':['有新角度']})
    assert result['counts']['rating'] == 5 and result['counts']['endorsed'] == 0
    assert memory.message_feedback.context('付费意愿','s') == []
    with pytest.raises(FeedbackError):
        memory.message_feedback.save(mid,'s',{'stance':'endorsed'})


@pytest.mark.parametrize('data', [{'rating':6},{'rating':True},{'rating':'5'},{'global_scope':'true'}, {'summary':'x'*501}, {'reasons':['invented']}, {'stance':'verified'}, {'inspiring':'yes'}])
def test_invalid_feedback_is_rejected(memory, data):
    with pytest.raises(FeedbackError):
        memory.message_feedback.save(answer(memory), 's', data)


def test_scope_is_server_derived_and_defaults_to_current_project(memory):
    mid = answer(memory, workspace='project-A')
    item = save(memory, mid, stance='endorsed', scope='global')
    assert item['scope'] == 'workspace:project-A'
    answer(memory, sid='same', workspace='project-A')
    answer(memory, sid='other', workspace='project-B')
    assert memory.message_feedback.context('付费意愿','same')
    assert not memory.message_feedback.context('付费意愿','other')
    save(memory, mid, feedback_id=item['feedback_id'], revision=item['revision'], global_scope=True)
    assert memory.message_feedback.context('付费意愿','other')


def test_no_workspace_is_session_scoped(memory):
    mid = answer(memory)
    assert save(memory, mid, stance='endorsed')['scope'] == 'session:s'
    assert memory.message_feedback.context('付费意愿','other') == []


def test_revision_history_idempotency_conflict_and_retraction(memory):
    mid = answer(memory)
    item = save(memory, mid, stance='endorsed')
    assert save(memory, mid, stance='endorsed')['revision'] == 1
    with pytest.raises(FeedbackError):
        save(memory, mid, stance='disputed')
    item = save(memory, mid, feedback_id=item['feedback_id'], revision=1, active=False)
    assert item['revision'] == 2 and not item['active']
    assert not memory.message_feedback.context('付费意愿','s')
    item = save(memory, mid, feedback_id=item['feedback_id'], revision=2, active=True)
    assert memory.message_feedback.context('付费意愿','s')
    assert memory.conn.execute('SELECT count(*) FROM message_feedback_events').fetchone()[0] == 3

def test_edited_message_can_be_reanchored_without_overwriting_history(memory):
    mid = 'turn:' + memory.save_turn('s','assistant','先验证付费意愿')
    old = save(memory, mid, stance='endorsed')
    assert memory.message_feedback.is_current(old['feedback_id'], old['revision'])
    memory.conn.execute("UPDATE turns SET content='先验证付费意愿，再决定' WHERE turn_id=?", (mid[5:],))
    memory.conn.commit()
    assert not memory.message_feedback.is_current(old['feedback_id'], old['revision'])
    result = memory.message_feedback.save(mid, 's', {'quote':'先验证付费意愿', 'stance':'endorsed'})
    assert len(result['items']) == 2
    assert result['counts']['endorsed'] == 1
    assert next(i for i in result['items'] if i['feedback_id'] == old['feedback_id'])['stale']


def test_retracted_evidence_is_not_exposed_as_endorsement_in_old_receipt(memory):
    mid = answer(memory)
    item = save(memory, mid, stance='endorsed')
    bundle = memory.personalization.context('付费意愿','s')
    memory.personalization.save_receipt('next-answer','s','',bundle,'原回复')
    save(memory, mid, feedback_id=item['feedback_id'], revision=1, active=False)
    assert memory.personalization.get_receipt('next-answer')['evidence'][0]['unavailable']
    assert not memory.personalization.set_feedback('next-answer','s',item['feedback_id'],'confirmed')


def test_hidden_deleted_wrong_session_and_wrong_author_sources(memory):
    mid = answer(memory)
    save(memory, mid, stance='endorsed')
    with pytest.raises(FeedbackError):
        memory.message_feedback.list(mid,'other')
    memory.set_message_state(mid, hidden=True)
    with pytest.raises(FeedbackError):
        memory.message_feedback.list(mid,'s')
    assert not memory.message_feedback.context('付费意愿','s')
    memory.delete_message_derived_data(mid)
    assert memory.conn.execute('SELECT count(*) FROM message_feedback_events').fetchone()[0] == 0
    eid = memory.session_log.append('s','user_message_received',{'input':'这是用户自己的消息'})
    with pytest.raises(FeedbackError):
        memory.message_feedback.save('event:'+eid,'s',{'rating':5})


def test_session_purge_removes_feedback_and_revision_copies(memory):
    mid = answer(memory)
    save(memory, mid, stance='endorsed', global_scope=True)
    memory.delete_session('s')
    assert memory.conn.execute('SELECT count(*) FROM message_feedback').fetchone()[0] == 0
    assert memory.conn.execute('SELECT count(*) FROM message_feedback_events').fetchone()[0] == 0


def test_edited_message_invalidates_old_anchor(memory):
    memory.save_turn('s','user','讨论')
    mid = 'turn:' + memory.save_turn('s','assistant','先验证付费意愿')
    save(memory,mid,stance='endorsed')
    memory.conn.execute("UPDATE turns SET content='先验证其他意见' WHERE turn_id=?", (mid[5:],)); memory.conn.commit()
    assert memory.message_feedback.list(mid,'s')['items'][0]['stale']
    assert not memory.message_feedback.context('付费意愿','s')


def test_service_and_websocket_reply_keep_transport_out_of_domain_logic(memory):
    mid = answer(memory)
    server = HushClawServer.__new__(HushClawServer); server._gateway = SimpleNamespace(memory=memory)
    ws = SimpleNamespace(send=AsyncMock())
    asyncio.run(server._dispatch(ws, {'type':'save_message_feedback','message_id':mid,'session_id':'s','request_id':'r',
        'feedback':{'quote':'先验证付费意愿','stance':'endorsed'}}))
    result = json.loads(ws.send.call_args.args[0])
    assert result['ok'] and result['request_id'] == 'r' and result['counts']['endorsed'] == 1
    asyncio.run(server._dispatch(ws, {'type':'save_message_feedback','message_id':mid,'session_id':'wrong','feedback':{'rating':5}}))
    assert not json.loads(ws.send.call_args.args[0])['ok']


def test_automatic_learning_rejects_assistant_authored_evidence(memory):
    from hushclaw.learning.reflection import TaskTrace
    user = '请评估付费意愿验证，不代表我认可这个观点。'
    eid = memory.session_log.append('s','user_message_received',{'input':user})
    mid = 'event:' + eid
    forged = {'title':'用户原则', 'body':'用户认可先验证付费意愿，再投入建设。','note_type':'belief', 'user_quote':'先验证付费意愿，再投入建设'}
    provider = SimpleNamespace(complete=AsyncMock(return_value=LLMResponse(content=json.dumps([forged]),stop_reason='end_turn')))
    ctl = LearningController(memory,provider=provider,agent_config=SimpleNamespace(model='test'))
    trace = TaskTrace(session_id='s',user_input=user,assistant_response='先验证付费意愿，再投入建设',source_message_id=mid)
    asyncio.run(ctl._extract_facts_llm(trace,'test',strict=True))
    assert memory.conn.execute('SELECT count(*) FROM notes').fetchone()[0] == 0
    assert trace.assistant_response not in provider.complete.call_args.kwargs['messages'][0].content
    assert not ctl._user_evidence(trace, {'user_quote':'伪造用户原话'})
    assert ctl._user_evidence(trace, {'user_quote':'请评估付费意愿验证'})
    # A quote is evidence of wording, not endorsement; no feedback is fabricated.
    assert memory.conn.execute('SELECT count(*) FROM message_feedback').fetchone()[0] == 0
