"""Keep database support claims aligned with maintained test coverage."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_readme_claims_only_ci_tested_database_backends() -> None:
    readme = (ROOT / "README.md").read_text()
    normalized_readme = " ".join(readme.split())

    assert (
        "GeneralManager builds on Django's database layer, but this project only "
        "claims backend support covered by its tests or maintained examples."
        in normalized_readme
    )
    assert (
        "SQLite, PostgreSQL, and MariaDB are exercised by CI and are fully supported."
        in normalized_readme
    )
    assert "any database supported by Django" not in normalized_readme
