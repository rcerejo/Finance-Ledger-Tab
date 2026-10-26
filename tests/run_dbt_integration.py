from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DBT_PROJECT = ROOT / "finance_dbt"


def run(command: list[str], environment: dict[str, str]) -> None:
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="finance-ledger-ci-") as directory:
        sandbox = Path(directory)
        output_dir = sandbox / "outputs"
        profiles_dir = sandbox / "profiles"
        output_dir.mkdir()
        profiles_dir.mkdir()
        shutil.copyfile(DBT_PROJECT / "profiles.example.yml", profiles_dir / "profiles.yml")

        database = output_dir / "finance_ci.sqlite"
        environment = os.environ.copy()
        environment["FINANCE_DB_PATH"] = str(database.resolve())
        environment["FINANCE_DB_DIR"] = str(output_dir.resolve())
        existing_pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = str(ROOT) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

        run([sys.executable, "tests/prepare_dbt_fixture.py", "--db", str(database)], environment)
        for command in ("seed", "run", "test"):
            run(
                [
                    "dbt",
                    command,
                    "--project-dir",
                    str(DBT_PROJECT),
                    "--profiles-dir",
                    str(profiles_dir),
                ],
                environment,
            )
        run([sys.executable, "tests/assert_dbt_fixture.py", "--db", str(database)], environment)


if __name__ == "__main__":
    main()
