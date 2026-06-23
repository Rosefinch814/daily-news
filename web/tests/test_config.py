from daily_news.config import load_section


def test_load_tech_section() -> None:
    section = load_section("tech")

    assert section.slug == "tech"
    assert section.publication_name == "我的日报·科技"
    assert len(section.enabled_sources) == 9
    assert any(source.id == "reuters_technology" and not source.enabled for source in section.sources)
    assert "英伟达" in section.interests.want.companies
    assert "小公司融资" in section.interests.avoid
