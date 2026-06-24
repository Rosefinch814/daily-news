from daily_news.config import load_pipeline_config, load_section


def test_load_tech_section() -> None:
    section = load_section("tech")

    assert section.slug == "tech"
    assert section.publication_name == "我的日报·科技"
    assert len(section.enabled_sources) == 9
    assert any(source.id == "reuters_technology" and not source.enabled for source in section.sources)
    assert "英伟达" in section.interests.want.companies
    assert "小公司融资" in section.interests.avoid


def test_load_pipeline_config() -> None:
    config = load_pipeline_config()

    assert config.ai.default_provider == "codex"
    assert config.ai.codex.command.endswith("codex exec")
    assert config.ai.claude.command == "claude -p"
    assert config.prompt.max_candidates == 60
