from sqlalchemy import select
from datetime import datetime, timezone

with factory() as s:
    stmt = select(DeviceConfig).where(DeviceConfig.key == 'omron_cuff_mac')
    existing = s.execute(stmt).scalar_one_or_none()
    if existing is None:
        s.add(DeviceConfig(
            key='omron_cuff_mac',
            value='<MAC>',
            updated_at=datetime.now(timezone.utc).isoformat(),
        ))
    else:
        existing.value = '<MAC>'
        existing.updated_at = datetime.now(timezone.utc).isoformat()
    s.commit()
