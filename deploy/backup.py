"""Local recovery copies. Off-server disaster recovery requires separate storage."""
from datetime import datetime, timezone
from pathlib import Path
import tarfile
import os
os.umask(0o077)
backup_dir=Path('/var/backups/southbound')
backup_dir.mkdir(parents=True,exist_ok=True)
name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
archive=backup_dir / (name+'.tar.gz')
with tarfile.open(archive,'w:gz') as tar:
    for folder in ['/var/lib/southbound/data','/var/lib/southbound/public','/etc/southbound']:
        tar.add(folder,arcname=folder.lstrip('/'))
for old in sorted(backup_dir.glob('*.tar.gz'),reverse=True)[7:]:
    old.unlink()
print('Backup created:',archive.name)
