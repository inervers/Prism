from pathlib import Path


def test_trufflehog_uses_supported_verified_results_flag():
    workflow = Path(".github/workflows/secrets-scan.yml").read_text(encoding="utf-8")

    assert "--only-verified" not in workflow
    assert "--results=verified" in workflow
