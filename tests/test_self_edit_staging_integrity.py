import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import eidos_staging


class TestSelfEditStagingIntegrity(unittest.TestCase):
    def test_schema_persists_validated_source_hash(self):
        with tempfile.TemporaryDirectory() as td:
            db=eidos_staging.StagingDB(Path(td)/"staging.db")
            conn=eidos_staging.get_conn(db.db_path)
            cols={row[1] for row in conn.execute("PRAGMA table_info(staged_changes)")}
            self.assertIn("production_sha256",cols)

    def test_promotion_rejects_source_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"repo"; root.mkdir()
            target=root/"sample.py"; target.write_text("x = 1\n",encoding="utf-8")
            state=Path(td)/"state"; state.mkdir()
            db=eidos_staging.StagingDB(state/"staging.db")
            change=eidos_staging.StagedChange(
                id="drift",file_path="sample.py",change_type="test",description="drift",
                original_code="x = 1",new_code="x = 2",diff="",
                staging_path=str(state/"sample.py"),production_path=str(target),
                status="validated",
                production_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            )
            db.save_change(change)
            target.write_text("x = 1\ny = 9\n",encoding="utf-8")
            system=object.__new__(eidos_staging.StagingSystem)
            system.db=db
            with patch.object(eidos_staging,"EIDOS_ROOT",root):
                self.assertFalse(system.promote_to_production("drift",backup_first=False))
            self.assertEqual(target.read_text(encoding="utf-8"),"x = 1\ny = 9\n")


if __name__=="__main__":
    unittest.main()
