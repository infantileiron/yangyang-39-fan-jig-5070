import json
from collections import Counter

from sqlalchemy import func, select

from src.domain.analysis import seed
from src.domain.demo import demo_extras
from src.models.base import Base, Session, engine, ensure_schema
from src.models.workflow import AnalysisRun, Correction, Observation, Review, Schedule
from src.services.corrections import correction_idem_key
from src.services.observations import clean_observation
from src.utils.keys import content_key


def seed_data():
    Base.metadata.create_all(engine)
    ensure_schema()
    rows = [clean_observation(row) for row in seed()]
    _px201, duplicate, correction_rows = demo_extras()
    rows.append(clean_observation(duplicate))
    counts = Counter(content_key(row) for row in rows)
    seen = Counter()
    with Session.begin() as db:
        keys = set(db.scalars(select(Observation.source_key)))
        for row in rows:
            key = content_key(row)
            seen[key] += 1
            if counts[key] > 1 and seen[key] > 1:
                key = f'{key}:{seen[key]}'
            if key not in keys:
                db.add(Observation(source_key=key, payload=row))

        existing_idem = set(db.scalars(select(Correction.idem_key)))
        for item in correction_rows:
            idem_key = correction_idem_key(item['trial'], item['basis'])
            if idem_key in existing_idem:
                continue
            basis_row = db.scalar(select(Observation).where(
                Observation.payload['trial'].as_string() == item['trial']).order_by(Observation.id))
            payload = basis_row.payload
            full_basis = {'trial': item['trial'], 'speed': payload['speed'], 'jig': payload['jig'], **item['basis']}
            db.add(Correction(
                idem_key=idem_key, trial=item['trial'], basis=item['basis'], corrected=item['corrected'],
                basis_key=content_key(full_basis), status=item['status'],
                created_by='analyst@demo.local', verified_by=item.get('verified_by'), note=item.get('note')))
        db.flush()
        return {model.__tablename__: db.scalar(select(func.count(model.id)))
                for model in (Observation, Correction, AnalysisRun, Review, Schedule)}


if __name__ == '__main__':
    print(json.dumps(seed_data(), sort_keys=True))
