from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_render_blueprint_enables_public_demo() -> None:
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert '      - key: DEMO_ENABLED\n        value: "true"' in blueprint
    assert '      - key: DEMO_ENABLED\n        value: "false"' not in blueprint
