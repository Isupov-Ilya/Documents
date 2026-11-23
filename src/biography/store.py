import sqlite3
from datetime import datetime
from pathlib import Path


class JobStore:
    def __init__(self, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "history.sqlite3")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, created TEXT, source TEXT, output TEXT UNIQUE, name TEXT, status TEXT)"
        )
        self.db.commit()

    def add(self, job_id, source, output, status="pending"):
        self.db.execute(
            "INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?,?)",
            (
                job_id,
                datetime.now().astimezone().isoformat(timespec="seconds"),
                str(source),
                str(output),
                "",
                status,
            ),
        )
        self.db.commit()

    def update(self, output, status, name=None):
        if name is None:
            self.db.execute(
                "UPDATE jobs SET status=? WHERE output=?", (status, str(output))
            )
        else:
            self.db.execute(
                "UPDATE jobs SET status=?,name=? WHERE output=?",
                (status, name, str(output)),
            )
        self.db.commit()

    def list(self, query=""):
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        text = "%" + escaped + "%"
        return self.db.execute(
            "SELECT id,created,source,output,name,status FROM jobs WHERE name LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\' OR created LIKE ? ESCAPE '\\' ORDER BY created DESC",
            (text, text, text),
        ).fetchall()

    def close(self):
        self.db.close()
