import hashlib
import json


def content_key(row):
    """按观测内容生成稳定的去重键，不依赖数据库。"""
    return 'data:' + hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
