"""读数更正的领域规则。

规则要点：
- 同一试片补抄耗电后会在台账留下两条完工记录，更正必须带原读数，才能指明改的是哪一条；
- 更正按「试片号 + 原读数」幂等，重试不新增记录；
- 已核实的更正锁定，晚到的旧记录不能冲掉；
- 依据相同却互相冲突的更正进入稳健性分析：每种可能读数都选出同一组合才照常推荐，
  推荐会变时结果页指出该复测的试片；
- 排队时刻冻结更正快照，排队后才到的更正留给下次分析。
"""
from collections import OrderedDict, defaultdict

from src.domain.analysis import eligible_choices
from src.utils.keys import content_key

VERIFIABLE_KEYS = ('warp', 'energy')


def correction_idem_key(trial, basis):
    return f"correction:{trial}:{content_key(basis)[len('data:'):]}"


def validate_correction(row):
    trial = (row.get('trial') or '').strip()
    if not trial:
        raise ValueError('请填写扇面试片号')
    basis = row.get('basis') or {}
    corrected = row.get('corrected') or {}
    if not isinstance(basis, dict) or not isinstance(corrected, dict):
        raise ValueError('原读数与更正读数需为记录对象')
    unknown = set(corrected) - set(VERIFIABLE_KEYS)
    if unknown:
        raise ValueError('更正只允许调整翘边与耗电，风档和夹具不能借更正修改')
    clean_basis, clean_corrected = {}, {}
    for key in VERIFIABLE_KEYS:
        if key in basis and basis[key] not in (None, ''):
            clean_basis[key] = _number(basis[key], key)
        if key in corrected and corrected[key] not in (None, ''):
            clean_corrected[key] = _number(corrected[key], key)
    if not clean_basis:
        raise ValueError('补录必须带原读数（翘边或耗电至少一项）')
    if not clean_corrected:
        raise ValueError('请填写至少一项更正后的翘边或耗电读数')
    if all(clean_basis.get(key) == clean_corrected[key] for key in clean_corrected):
        raise ValueError('更正读数与原读数完全相同，无需登记更正')
    return trial, clean_basis, clean_corrected


def _number(value, label='读数'):
    if value in (None, ''):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} 必须是数字') from exc
    if number != number or number in (float('inf'), float('-inf')) or number < 0:
        raise ValueError(f'{label} 必须是非负有限数字')
    return number


def snapshot_correction(correction):
    """排队时刻冻结的更正快照（不依赖 ORM 实例，可直接 JSON 落库）。"""
    return {
        'id': correction.id, 'trial': correction.trial, 'basis': dict(correction.basis),
        'corrected': dict(correction.corrected), 'basis_key': correction.basis_key,
        'status': correction.status,
    }


def _row_key(row):
    return content_key({key: row[key] for key in ('trial', 'speed', 'jig', 'warp', 'energy')})


def resolve_observations(rows, correction_snapshot):
    """把台账观测与已核实更正合并成分析用的每个试片一条读数。

    台账里同一试片的多条完工记录：只有带匹配原读数（basis_key 命中）的已核实更正
    才能把它们并成一条；并不掉就原样抛出，分析停下并提示补录/复测。
    待核实更正不替换主读数，只用于稳健性分析。
    """
    verified = [item for item in correction_snapshot if item['status'] == 'verified']
    by_trial = defaultdict(list)
    for row in rows:
        by_trial[row['trial']].append(row)

    resolved, duplicate_trials = [], []
    for trial, ledger in by_trial.items():
        match = next((item for item in verified if item['trial'] == trial
                      and item['basis_key'] in {_row_key(row) for row in ledger}), None)
        if len(ledger) > 1 and match is None:
            duplicate_trials.append(trial)
            continue
        if match is not None:
            if len(ledger) > 2:
                # 一条已核实更正只能解释一对重复记录；更多旧行需要人工复核，不能静默丢弃
                duplicate_trials.append(trial)
                continue
            base = next(row for row in ledger if _row_key(row) == match['basis_key'])
            merged = dict(base)
            merged.update(match['corrected'])
            resolved.append(merged)
        else:
            resolved.extend(ledger)

    if duplicate_trials:
        names = '、'.join(sorted(duplicate_trials))
        raise ValueError(f'试片 {names} 存在多条完工记录且没有带匹配原读数的已核实更正，请补录或复测后再算')

    seen = set()
    for row in resolved:
        if row['trial'] in seen:
            raise ValueError(f"试片 {row['trial']} 仍有多条完工记录")
        seen.add(row['trial'])
    return resolved


def robust_review(resolved_rows, correction_snapshot, parameters):
    """依据相同却互相冲突的更正：枚举每个待核实试片的每种可能读数，检查推荐是否稳健。"""
    pending = [item for item in correction_snapshot if item['status'] == 'pending']
    if not pending:
        return {'robust': True, 'ambiguous_trials': [], 'scenario_count': 1, 'retest_trials': [],
                'note': '没有待核实更正，推荐基于当前已核实读数。'}

    by_trial = OrderedDict()
    for item in pending:
        by_trial.setdefault(item['trial'], []).append(item)
    ambiguous = {trial: items for trial, items in by_trial.items()
                 if len({content_key(dict(item['corrected'])) for item in items}) > 1}
    if not ambiguous:
        return {'robust': True, 'ambiguous_trials': [], 'scenario_count': 1, 'retest_trials': [],
                'note': '待核实更正读数一致，不影响比选。'}

    primary = eligible_choices(resolved_rows, parameters.get('energy_limit', 15))
    primary_winner = primary[0][1] if primary else None
    limit = parameters.get('energy_limit', 15)

    def winner_for(trial, corrected):
        scenario = [dict(row, **corrected) if row['trial'] == trial else dict(row) for row in resolved_rows]
        choices = eligible_choices(scenario, limit)
        return choices[0][1] if choices else None

    # 先定位单试片就能翻转推荐的试片；都是同一依据下的冲突候选
    flipping = []
    details = []
    for trial, items in ambiguous.items():
        candidate_winners = {winner_for(trial, dict(item['corrected'])) for item in items}
        candidates = sorted({tuple(sorted(item['corrected'].items())) for item in items})
        details.append({'trial': trial, 'candidates': [dict(pairs) for pairs in candidates],
                        'winners': sorted(w for w in candidate_winners if w)})
        if primary_winner is not None and candidate_winners != {primary_winner}:
            flipping.append(trial)

    # 交叉枚举全部可能组合，确认整体推荐集合
    trial_order = list(ambiguous)
    winners = set()

    def enumerate_scenarios(index, working):
        if index == len(trial_order):
            choices = eligible_choices(working, limit)
            winners.add(choices[0][1] if choices else None)
            return
        trial = trial_order[index]
        variants = {content_key(dict(item['corrected'])): dict(item['corrected'])
                    for item in ambiguous[trial]}
        for corrected in variants.values():
            enumerate_scenarios(index + 1,
                                [dict(row, **corrected) if row['trial'] == trial else row for row in working])

    enumerate_scenarios(0, resolved_rows)
    scenario_count = 1
    for items in ambiguous.values():
        scenario_count *= len({content_key(dict(item['corrected'])) for item in items})
    robust = winners == {primary_winner}
    retest = sorted(flipping) if flipping else ([] if robust else sorted(ambiguous))
    note = ('每个待核实试片的每种可能读数都选出同一组合，推荐照常。'
            if robust else '存在依据相同却互相冲突的更正，推荐随读数变化，请先复测表中试片再签核。')
    return {'robust': robust, 'ambiguous_trials': sorted(ambiguous), 'scenario_count': scenario_count,
            'retest_trials': retest, 'details': details, 'possible_winners': sorted(w for w in winners if w),
            'primary_winner': primary_winner, 'note': note}
