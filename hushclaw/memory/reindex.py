"""Repair derived embedding indexes without changing original memories.

python -m hushclaw.memory.reindex [--apply] [--limit 1000]
"""
from __future__ import annotations

import argparse
import json


def repair_index(memory, *, apply=False, limit=1000):
    store = memory._vec
    probe = store._embed('semantic index health check')
    if not probe:
        return {'ok': False, 'reason': 'embedding_unavailable', 'repaired': 0}
    rows = memory.conn.execute('''SELECT n.note_id,n.title,b.body FROM notes n
        JOIN note_bodies b ON b.note_id=n.note_id LEFT JOIN embeddings e ON e.note_id=n.note_id
        WHERE e.note_id IS NULL OR e.model<>? OR e.dim<>? LIMIT ?''',
        (store._model_key, len(probe), max(1, limit))).fetchall()
    repaired = 0
    if apply:
        for row in rows:
            if not store.index(row['note_id'], (row['title'] or '') + '\n' + row['body']):
                break  # Never write a different fallback space into this index.
            repaired += 1
        memory._recall_cache.clear()
    return {'ok': not apply or repaired == len(rows), 'dimension': len(probe),
            'candidates': len(rows), 'repaired': repaired, 'limit': limit}


def main():
    from hushclaw.config import load_config
    from hushclaw.memory.store import MemoryStore
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--limit', type=int, default=1000)
    args = parser.parse_args()
    cfg = load_config()
    memory = MemoryStore(cfg.memory.data_dir, embed_provider=cfg.memory.embed_provider,
                         embed_model=cfg.memory.embed_model, api_key=cfg.provider.api_key,
                         database_encryption=cfg.memory.database_encryption)
    try:
        print(json.dumps(repair_index(memory, apply=args.apply, limit=args.limit)))
    finally:
        memory.close()


if __name__ == '__main__':
    main()
