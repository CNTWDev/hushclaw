from pathlib import Path


def test_agentos_boundary_adrs_are_present_and_current():
    root = Path(__file__).resolve().parents[1]
    runtime_adr = root / "docs" / "adr" / "ADR-0011-agentos-context-runtime-boundaries.md"
    distro_adr = root / "docs" / "adr" / "ADR-0009-kernel-distro-contract.md"
    packaging_adr = root / "docs" / "adr" / "ADR-0008-distro-boundary.md"

    runtime_text = runtime_adr.read_text(encoding="utf-8")
    distro_text = distro_adr.read_text(encoding="utf-8")
    packaging_text = packaging_adr.read_text(encoding="utf-8")

    for term in (
        "PromptBlockRegistry",
        "ToolOutputBudget",
        "SessionRecall",
        "ContextTrace",
        "read_artifact",
        "domain/package.py",
    ):
        assert term in runtime_text
    assert "prompt_blocks()" in distro_text
    assert "The Four Distro Extension Surfaces" in distro_text
    assert "`team` is intentionally not a distro" in packaging_text
    assert "TeamDistro" not in packaging_text
