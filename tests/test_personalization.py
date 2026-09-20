import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from hushclaw.learning.personal_worker import PersonalLearningWorker
from hushclaw.memory.personalization import PersonalizationStore
from hushclaw.memory.reindex import repair_index
from hushclaw.memory.store import MemoryStore
from hushclaw.memory.vectors import _local_embed, _tokenize
from hushclaw.providers.base import LLMResponse
from hushclaw.util.tokens import estimate_tokens


@pytest.fixture
def memory(tmp_path):
    m = MemoryStore(tmp_path)
    yield m
    m.close()


def source(memory, text='我希望先分析约束条件，再讨论解决方案。', sid='s'):
    eid = memory.session_log.append(sid, 'user_message_received', {'input': text})
    return 'event:' + eid


def preference(memory, mid='', **kwargs):
    return memory.user_profile.upsert_fact(category='communication_style', key=kwargs.get('key', '结构清晰'),
        value={'summary': kwargs.get('text', '先分析约束条件，再讨论解决方案。')}, confidence=.9,
        source_session_id='s', source_message_id=mid)


def test_local_vectors_are_stable_across_processes_and_segment_chinese():
    code = 'from hushclaw.memory.vectors import _local_embed;print(_local_embed("分析约束条件"))'
    outputs = [subprocess.check_output([sys.executable, '-c', code], env={**os.environ, 'PYTHONHASHSEED': str(n)}) for n in (1, 2)]
    assert outputs[0] == outputs[1]
    assert '约束' in _tokenize('先分析约束条件')


def test_failed_remote_embedding_never_writes_local_under_remote_name(memory):
    memory._vec.embed_provider = 'ollama'
    memory._vec._model_key = 'ollama:bge-m3'
    with patch('hushclaw.memory.vectors._ollama_embed', return_value=None):
        assert not memory._vec.index('missing', 'test')
        assert memory._vec.search('test') == []
    assert memory.conn.execute('SELECT count(*) FROM embeddings').fetchone()[0] == 0


def test_remote_embedding_input_is_bounded_without_mutating_note(memory):
    memory._vec.embed_provider = 'ollama'
    with patch('hushclaw.memory.vectors._ollama_embed', return_value=[1.0, 0.0]) as embed:
        memory._vec._embed('开头' + '正文' * 10000 + '结尾')
        text = embed.call_args.args[0]
        assert len(text) <= 4000 and text.startswith('开头') and text.endswith('结尾')


def test_index_repair_preserves_original_note(memory):
    nid = memory.remember('重要的长期方法论', persist_to_disk=False)
    memory.conn.execute("UPDATE embeddings SET model='legacy',dim=3 WHERE note_id=?", (nid,))
    memory.conn.commit()
    assert repair_index(memory)['candidates'] == 1
    assert repair_index(memory, apply=True)['repaired'] == 1
    assert memory.get_note(nid)['body'] == '重要的长期方法论'


def test_receipt_has_only_actual_bounded_context(memory):
    mid = source(memory)
    preference(memory, mid)
    bundle = memory.personalization.context('请分析约束条件', 's', max_tokens=250)
    assert bundle['evidence']
    assert sum(estimate_tokens(json.dumps(PersonalizationStore.prompt_item(e), ensure_ascii=False)) for e in bundle['evidence']) <= 250
    text = memory.personalization.render(bundle)
    assert 'not instructions' in text
    assert all(e['text'] in text for e in bundle['evidence'])
    memory.personalization.save_receipt('answer', 's', mid, bundle, '先分析约束条件。')
    got = memory.personalization.get_receipt('answer', 's')
    assert got['status'] == 'pending'
    assert got['evidence'] == bundle['evidence']
    assert 'answer' not in got
    assert memory.personalization.get_receipt('answer', 'another-session') is None


def test_feedback_is_scoped_validated_and_removes_future_reference(memory):
    fid = preference(memory)
    bundle = memory.personalization.context('方法论')
    memory.personalization.save_receipt('a', 's', '', bundle, 'answer')
    eid = 'profile:' + fid
    assert not memory.personalization.set_feedback('a', 'other', eid, 'rejected')
    assert not memory.personalization.set_feedback('a', 's', 'made-up', 'rejected')
    assert memory.personalization.set_feedback('a', 's', eid, 'rejected')
    assert all(e['id'] != eid for e in memory.personalization.context('方法论')['evidence'])
    assert memory.personalization.set_feedback('a', 's', eid, 'unconfirmed')
    assert any(e['id'] == eid for e in memory.personalization.context('方法论')['evidence'])


def test_hidden_sources_never_leak_through_receipts(memory):
    mid = source(memory)
    preference(memory, mid)
    bundle = memory.personalization.context('方法论')
    memory.personalization.save_receipt('a', 's', mid, bundle, 'answer')
    assert memory.set_message_state(mid, hidden=True)
    assert memory.personalization.context('方法论')['evidence'] == []
    item = memory.personalization.get_receipt('a')['evidence'][0]
    assert item['unavailable'] and not item['source_excerpt']


def test_purge_removes_derived_receipt_and_job(memory):
    mid = source(memory)
    memory.personalization.save_receipt('a', 's', mid, {'question':'private', 'evidence':[]}, 'answer')
    memory.conn.execute("INSERT INTO learning_jobs(job_id,payload) VALUES(?, '{}')", (mid,))
    memory.conn.commit()
    memory.delete_message_derived_data(mid)
    assert memory.personalization.get_receipt('a') is None
    assert memory.conn.execute('SELECT count(*) FROM learning_jobs').fetchone()[0] == 0


def test_profile_update_can_reduce_confidence(memory):
    fid = preference(memory)
    memory.user_profile.upsert_fact(category='communication_style', key='结构清晰', value={'summary':'仅在报告中适用'}, confidence=.5)
    assert memory.user_profile.list_facts()[0]['confidence'] == .5


def test_confirmation_does_not_follow_rewritten_profile(memory):
    fid = preference(memory)
    memory.conn.execute("INSERT INTO memory_feedback VALUES(?, 'confirmed', 1)", ('profile:' + fid,))
    memory.conn.commit()
    preference(memory, text='报告需要详细，闲聊需要简短。')
    assert 'profile:' + fid not in memory.personalization.feedback()


def test_expired_final_attempt_is_marked_failed(memory):
    memory.personalization.save_receipt('a', 's', '', {'question':'问题', 'evidence':[]}, '回答')
    memory.conn.execute("UPDATE understanding_receipts SET status='running', attempts=3, next_attempt=0")
    memory.conn.commit()
    asyncio.run(PersonalLearningWorker(memory, SimpleNamespace()).tick())
    assert memory.personalization.get_receipt('a')['status'] == 'failed'


def test_review_does_not_restore_evidence_purged_during_provider_call(memory):
    mid = source(memory)
    preference(memory, mid)
    bundle = memory.personalization.context('方法论')
    memory.personalization.save_receipt('a', 's', '', bundle, '回答')
    async def complete(**kwargs):
        memory.personalization.forget_source(mid)
        return LLMResponse(content='{"positions":[],"links":[]}', stop_reason='end_turn')
    learning = SimpleNamespace(provider=SimpleNamespace(complete=complete), agent_config=SimpleNamespace(model='test'))
    asyncio.run(PersonalLearningWorker(memory, learning).tick())
    receipt = memory.personalization.get_receipt('a')
    assert receipt['evidence'] == [] and receipt['status'] == 'pending'


def test_opinion_identity_and_idempotency_preserve_evolution(memory):
    one = source(memory)
    two = source(memory, '探索阶段先发散，决策阶段再分析约束')
    old = memory.upsert_opinion_event(topic='约束优先', stance_delta='先分析约束', source_message_id=one)
    updated = memory.upsert_opinion_event(topic='换一种说法', thread_id=old['thread_id'], event_type='refine',
        stance_delta='探索阶段先发散，决策阶段再分析约束', reason='区分决策阶段', source_message_id=two)
    assert updated['thread_id'] == old['thread_id']
    memory.upsert_opinion_event(topic='再换一种说法', thread_id=old['thread_id'], stance_delta='duplicate', source_message_id=two)
    assert memory.conn.execute('SELECT count(*) FROM opinion_events').fetchone()[0] == 2
    bundle = memory.personalization.context('如何分析约束条件')
    assert any(e['kind'] == 'viewpoint' and len(e['evolution']) == 2 for e in bundle['evidence'])


def test_workspace_notes_do_not_cross_scope(memory):
    memory.remember('私有项目约束条件', scope='workspace:private', persist_to_disk=False)
    assert memory.personalization.context('项目约束条件', scopes=['global', 'workspace:public'])['evidence'] == []


def test_unrelated_generic_preferences_do_not_masquerade_as_style(memory):
    memory.user_profile.upsert_fact(category='preferences', key='ai_entry_point',
        value={'summary':'Interested in AI market entry strategy.'})
    assert memory.personalization.context('如何分辨明确观点与系统推断')['evidence'] == []


def test_unavailable_profile_does_not_starve_valid_preferences(memory):
    for n in range(8):
        preference(memory, 'event:missing' + str(n), key='missing' + str(n), text='缺失来源' + str(n))
    valid = preference(memory, source(memory))
    assert any(e['id'] == 'profile:' + valid for e in memory.personalization.context('方法论')['evidence'])


def test_short_followup_uses_previous_question_not_already_persisted_current(memory):
    memory.save_turn('s', 'user', '请比较这两个产品的成本与约束')
    memory.save_turn('s', 'user', '继续比较')
    bundle = memory.personalization.context('继续比较', 's')
    assert '产品的成本与约束' in bundle['retrieval_query']


def test_review_rejects_fabricated_ids_and_nonliteral_spans():
    payload = {'question': '请分析约束条件，我希望先比较再决策。', 'answer': '先比较成本与约束，再作出决策。', 'evidence': [{'id': 'valid'}]}
    result = PersonalizationStore.validate_analysis({'positions': [
        {'quote': '我希望先比较再决策', 'interpretation': '比较优先'}, {'quote':'用户喜欢冒险'}],
        'links': [{'id':'fake', 'answer_quote':'先比较成本与约束'},
                  {'id':'valid', 'answer_quote':'没有说过的东西', 'relation':'used'},
                  {'id':'valid', 'answer_quote':'先比较成本与约束', 'relation':'used'}]}, payload)
    assert len(result['positions']) == 1
    assert len(result['links']) == 1


def test_background_receipt_review_is_durable_and_retries(memory):
    memory.personalization.save_receipt('a', 's', '', {'question':'只是在提问吗？', 'evidence':[]}, '这只是一个问题。')
    provider = SimpleNamespace(complete=AsyncMock(side_effect=[RuntimeError('offline'), LLMResponse(content=json.dumps({'question_summary':'问题', 'positions':[], 'links':[]}), stop_reason='end_turn')]))
    learning = SimpleNamespace(provider=provider, agent_config=SimpleNamespace(model='test', cheap_model=''))
    worker = PersonalLearningWorker(memory, learning)
    asyncio.run(worker.tick())
    assert memory.personalization.get_receipt('a')['status'] == 'pending'
    memory.conn.execute('UPDATE understanding_receipts SET next_attempt=0'); memory.conn.commit()
    asyncio.run(PersonalLearningWorker(memory, learning).tick())
    receipt = memory.personalization.get_receipt('a')
    assert receipt['status'] == 'ready' and receipt['analysis']['positions'] == []


def test_understanding_api_checks_session_and_hidden_message(memory):
    from hushclaw.server import HushClawServer
    mid = source(memory)
    preference(memory, mid)
    bundle = memory.personalization.context('方法论')
    memory.personalization.save_receipt(mid, 's', '', bundle, 'answer')
    server = HushClawServer.__new__(HushClawServer)
    server._gateway = SimpleNamespace(memory=memory)
    ws = SimpleNamespace(send=AsyncMock())
    def call(sid):
        asyncio.run(server._dispatch(ws, {'type':'get_understanding', 'message_id':mid, 'session_id':sid}))
        return json.loads(ws.send.call_args.args[0])
    assert call('s')['receipt']['evidence']
    assert not call('wrong')['ok']
    memory.set_message_state(mid, hidden=True)
    assert not call('s')['ok']
