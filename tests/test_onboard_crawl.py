import gzip

from onboard_fakes import FIX, HOST, FakeClient, Resp, gear_urls, species_urls, site_files

from nicheclock.onboard import crawl as C


def test_parse_sitemap_index_and_urlset():
    is_index, locs = C.parse_sitemap((FIX / "sitemap_index.xml").read_text())
    assert is_index
    assert locs[0] == f"{HOST}/birds-sitemap.xml" and len(locs) == 5
    is_index, locs = C.parse_sitemap((FIX / "urlset_cdata.xml").read_text())
    assert not is_index
    # CDATA unwrapped, &amp; unescaped, image:loc is also a <loc>-like tag only when named <loc>
    assert f"{HOST}/guides/kestrel-patch-notes?ref=a&b=1" in locs
    assert f"{HOST}/guides/nuthatch-tier-list&x=1" in locs


def test_parse_sitemap_plain_text_list():
    assert C.parse_sitemap("https://a.test/x\nnot a url\nhttps://a.test/y\n") == (
        False, ["https://a.test/x", "https://a.test/y"])


def test_decode_sitemap_gzip_by_magic_number():
    raw = (FIX / "urlset_cdata.xml").read_bytes()
    assert C.decode_sitemap(gzip.compress(raw)) == raw.decode()
    assert C.decode_sitemap(raw) == raw.decode()
    assert C.decode_sitemap(b"\x1f\x8b garbage") == ""


def test_parse_page_title_h1_and_site_name():
    title, h1, name = C.parse_page((FIX / "page.html").read_text())
    assert title == "Snowy Owl Best Builds & Tier List | Birdsite"
    assert h1 == "Snowy Owl best builds"      # the logo <h1> that repeats og:site_name is skipped
    assert name == "Birdsite"


def test_sample_urls_is_deterministic_and_spans_sections():
    urls = species_urls() + gear_urls()
    a = C.sample_urls(urls, 20)
    assert a == C.sample_urls(list(reversed(urls)), 20)
    assert len(a) == len(set(a)) == 20
    assert any("/birds/" in u for u in a) and any("/gear/" in u for u in a)
    assert C.sample_urls(urls[:5], 20) == sorted(urls[:5])
    assert C.sample_urls(urls, 0) == []


def test_sample_urls_flat_site_is_one_section():
    flat = [f"{HOST}/post-{i}" for i in range(100)]
    pick = C.sample_urls(flat, 10)
    assert len(set(pick)) == 10


def test_crawl_site_reads_index_gz_and_skips_junk_children():
    client = FakeClient(site_files())
    c = C.crawl_site(client, "birdsite.test", max_pages=12)
    assert c.host == "birdsite.test"
    # robots Sitemap: line for another host is ignored; image/category/author children are never fetched
    assert f"{HOST}/sitemap_index.xml" in c.sitemaps and f"{HOST}/gear-sitemap.xml.gz" in c.sitemaps
    assert not any("image-sitemap" in r or "category-sitemap" in r or "author-sitemap" in r for r in client.requests)
    assert not any("other.test" in r for r in client.requests)
    assert set(c.urls) == set(species_urls() + gear_urls())
    assert c.fetches == 12 and len(c.pages) == 12
    assert c.pages[0].url == f"{HOST}/"
    assert {p.url for p in c.pages[1:]} <= set(c.urls)
    titles = c.titles()
    some = next(p.url for p in c.pages[1:])
    assert "Birdsite" not in titles[some][0]       # brand suffix stripped


def test_crawl_respects_page_budget_and_never_fetches_twice():
    client = FakeClient(site_files())
    C.crawl_site(client, HOST, max_pages=50)
    pages = [r for r in client.requests if "/birds/" in r or "/gear/" in r]
    assert len(pages) == len(set(pages))
    assert len(pages) + 1 <= 50                    # + homepage


def test_crawl_skips_robots_disallowed_pages():
    files = site_files()
    client = FakeClient(files, disallow=(f"{HOST}/gear/",))
    c = C.crawl_site(client, HOST, max_pages=30)
    assert all("/gear/" not in p.url for p in c.pages)
    assert any("disallows" in w for w in c.warnings)


def test_crawl_without_sitemap_uses_homepage_links():
    client = FakeClient(site_files(with_sitemap=False))
    c = C.crawl_site(client, HOST, max_pages=10)
    assert any("no sitemap" in w for w in c.warnings)
    assert len(c.urls) == 5 and all("tier-list" in u for u in c.urls)
    assert len(c.pages) == 6                       # homepage + 5 linked pages


def test_crawl_survives_non_html_and_errors():
    files = site_files()
    files[species_urls()[0]] = Resp(200, b"%PDF", {"content-type": "application/pdf"})
    files[species_urls()[1]] = Resp(500, b"boom")
    c = C.crawl_site(FakeClient(files), HOST, max_pages=100)
    got = {p.url for p in c.pages}
    assert species_urls()[0] not in got and species_urls()[1] not in got
    assert c.fetches == 1 + len(species_urls()) + len(gear_urls())
