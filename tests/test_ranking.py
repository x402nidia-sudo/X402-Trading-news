from datetime import datetime,timedelta,timezone
from server.news.ranking import rank_articles,canonical_url
from server.news.catalog import get_asset

def article(title="Bitcoin blockchain security upgrade", url="https://wire.example/story", provider="guardian", hours=1):
    return {"title": title, "url": url, "provider": provider, "source": "Test Wire",
            "summary": "Bitcoin protocol upgrade", "published_at": (datetime.now(timezone.utc)-timedelta(hours=hours)).isoformat()}

def test_relevance_not_article_count():
    items = [article("Gardening weather", f"https://other.example/{i}") for i in range(10)]
    for a in items:a["summary"]="Flowers and trees"
    ranked, stats=rank_articles(items+[article()], get_asset("BTC"))
    assert len(ranked)==1 and stats["excluded"]["irrelevant"]==10
    assert "recommendation" not in ranked[0]

def test_ambiguous_avalanche_does_not_mean_avax():
    a=article("Avalanche shuts mountain road");a["summary"]="Snow and travel"
    assert rank_articles([a],get_asset("AVAX"))[0]==[]

def test_duplicate_same_publisher_across_apis_not_extra_confirmation():
    a=article(); b=article(url=a["url"]+"?utm_source=feed",provider="gnews")
    ranked,stats=rank_articles([a,b],get_asset("BTC"))
    assert len(ranked)==1 and stats["duplicates"]==1
    assert ranked[0]["coverage_domains"]==1 and ranked[0]["components"]["coverage"]==0

def test_different_sources_coverage_does_not_double_article_count():
    ranked,stats=rank_articles([article(),article(url="https://another.example/story")],get_asset("BTC"))
    assert len(ranked)==1 and ranked[0]["coverage_domains"]==2
    assert stats["duplicates"]==1

def test_opposing_news_stays_separate():
    items=[article("Bitcoin ETF application approved by federal regulators",url="https://a.example/1"),
           article("Bitcoin ETF application rejected by federal regulators",url="https://b.example/1")]
    assert len(rank_articles(items,get_asset("BTC"))[0])==2

def test_stale_future_undated_and_unsafe_urls_rejected():
    old=article(hours=70);future=article(hours=-2);undated=article();undated["published_at"]=None
    evil=article(url="javascript:alert(1)")
    ranked,stats=rank_articles([old,future,undated,evil],get_asset("BTC"))
    assert not ranked and stats["excluded"]=={"irrelevant":0,"old_or_undated":3,"invalid":1}

def test_tracking_removed_but_article_identity_preserved():
    assert canonical_url("https://x.example/news?id=123&utm_source=a#top")=="https://x.example/news?id=123"
