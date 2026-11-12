"""Synthetic API/HTML contracts. No real vacancies, keys or network calls."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from career_scout import cli
from career_scout.adapters import HabrClient, SuperJobClient, TrudvsemClient, habr_detail
from career_scout.collector import collect
from career_scout.common import SourceError
from career_scout.filtering import evaluate
from career_scout.hh import Credentials
from career_scout.models import source_identity
from career_scout.search_config import load_config, plan
from career_scout.source_normalization import superjob_record, trudvsem_record
from career_scout.storage import Store

CONFIG = Path(__file__).parents[1] / "configs/search.toml"
START = "2026-11-01T00:00:00+00:00"
END = "2026-11-11T00:00:00+00:00"


def trud(vid="1", **extra):
    return {
        "id": vid,
        "company": {"companycode": "company", "name": "Synthetic IT"},
        "job-name": "Junior аналитик",
        "duty": "<p>Разрабатывать цифровой продукт.</p>",
        "requirements": "<p>Требования к системе.</p>",
        "requirement": {"experience": 3, "qualification": "Ведение документации."},
        "salary_min": None,
        "salary_max": None,
        "creation-date": "2026-11-01",
        "date_modify": "2026-11-10T12:00:00+0300",
        **extra,
    }


def sj(vid=1, **extra):
    return {
        "id": vid,
        "profession": "Junior аналитик",
        "firm_name": "Synthetic IT",
        "work": "<p>Задачи продукта</p>",
        "candidat": "Требования к интеграциям",
        "compensation": "Условия работы",
        "vacancyRichText": "<p>Задачи продукта</p>",
        "agreement": True,
        "payment_from": 0,
        "payment_to": 0,
        "experience": {"id": 3},
        "date_published": 1794340800,
        **extra,
    }


def habr(vid=1, **extra):
    return {
        "id": vid,
        "title": "Аналитик",
        "company": {"title": "Synthetic IT"},
        "qualification": "Junior",
        "description": "<p>Весь текст продукта.</p><ul><li>Работа с API</li></ul>",
        "salary": {"from": None, "to": None, "currency": None},
        "remoteWork": True,
        **extra,
    }


def html_card(vid=1, **extra):
    v = habr(vid, **extra)
    job = {
        "@type": "JobPosting",
        "identifier": {"value": str(vid)},
        "title": v["title"],
        "description": v["description"],
        "datePosted": "2026-11-01",
        "hiringOrganization": {"name": v["company"]["title"]},
    }
    return (
        '<html><script data-ssr-state="true" type="application/json">'
        + json.dumps({"vacancy": v})
        + '</script><script type="application/ld+json">'
        + json.dumps(job)
        + "</script></html>"
    )


def client(source, tmp_path, handler, **kwargs):
    options = {
        "max_requests": 30,
        "retries": 0,
        "interval": 0,
        "client": httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    }
    if source == "superjob":
        env = tmp_path / "test.env"
        env.write_text("SUPERJOB_APP_KEY=fake-key\n")
        return SuperJobClient(Credentials(env), **options)
    return {"trudvsem": TrudvsemClient, "habr": HabrClient}[source](**options)


def listing(source, page, ids, *, total=3, per_page=2):
    if source == "trudvsem":
        return {
            "status": "200",
            "meta": {"total": total, "limit": per_page},
            "results": {"vacancies": [{"vacancy": trud(str(i))} for i in ids]},
        }
    if source == "superjob":
        return {
            "objects": [sj(i) for i in ids],
            "total": total,
            "more": (page + 1) * per_page < total,
        }
    return {
        "list": [habr(i) for i in ids],
        "meta": {
            "totalResults": total,
            "perPage": per_page,
            "currentPage": page + 1,
            "totalPages": (total + per_page - 1) // per_page,
        },
    }


def test_text_sections_missing_salary_and_minimum_three_years_are_preserved():
    policy = load_config(CONFIG).filters
    record = trudvsem_record(
        trud(), source_identity("trudvsem", "1", company_code="company").source_id
    )
    assert all(
        x in record["description"] for x in ["Разрабатывать", "Требования", "Ведение документации"]
    )
    assert record["attributes"]["salary"] is None
    exp = record["attributes"]["experience"]
    assert exp["min_years"] == 3 and exp["max_years"] is None and exp["category"] is None
    assert evaluate(record, policy).status == "KEEP"
    record = superjob_record(sj(), "1")
    assert all(x in record["description"] for x in ["Задачи", "Требования", "Условия"])
    assert record["description"].count("Задачи продукта") == 1
    assert record["attributes"]["salary"] is None
    assert evaluate(record, policy).status == "KEEP"
    assert record["source_updated_at"] is None


def test_habr_uses_current_detail_conditions_and_keeps_ld_provenance():
    record = habr_detail(html_card(salary={"from": 50000, "to": None, "currency": "rub"}), "1")
    assert record["description"].count("Весь текст") == 1
    assert record["attributes"]["salary"]["amount_from"] == 50000
    assert record["attributes"]["salary"]["gross"] is None
    assert record["attributes"]["salary"]["period"] is None
    assert record["source_updated_at"] is None
    assert record["published_at"] == "2026-11-01"
    assert record["attributes"]["work_formats"] == ["remote"]
    assert record["source_payload"]["job_posting"]["identifier"]["value"] == "1"
    assert evaluate(record, load_config(CONFIG).filters).criteria["grade"].status == "allowed"
    record["source_payload"]["vacancy"]["qualification"] = "Senior"
    assert evaluate(record, load_config(CONFIG).filters).criteria["grade"].status == "excluded"
    record["title"] = "Junior analyst"
    assert evaluate(record, load_config(CONFIG).filters).criteria["grade"].status == "conflict"


@pytest.mark.parametrize("source", ["trudvsem", "habr", "superjob"])
def test_page_cursor_restart_and_no_duplicate_details(source, tmp_path):
    calls = []

    def handler(req):
        calls.append((req.url.path, dict(req.url.params)))
        if "keyword" in req.url.params or "text" in req.url.params or "q" in req.url.params:
            key = "offset" if source == "trudvsem" else "page"
            page = int(req.url.params[key]) - (1 if source == "habr" else 0)
            assert not {"salary", "only_with_salary", "experience", "no_agreement"} & set(
                req.url.params
            )
            return httpx.Response(200, json=listing(source, page, [1, 2] if page == 0 else [2, 3]))
        vid = req.url.path.rstrip("/").split("/")[-1]
        if source == "habr":
            return httpx.Response(200, text=html_card(int(vid)))
        if source == "superjob":
            assert req.headers["X-Api-App-Id"] == "fake-key"
            return httpx.Response(200, json=sj(int(vid)))
        return httpx.Response(
            200,
            json={
                "status": "200",
                "meta": {"total": 1, "limit": 100},
                "results": {"vacancies": [{"vacancy": trud(vid)}]},
            },
        )

    store = Store(tmp_path / "db.sqlite3")
    frozen = plan(
        load_config(CONFIG),
        source=source,
        queries=["аналитик"],
        per_page=2,
        now=datetime(2026, 11, 11, tzinfo=UTC),
    )
    frozen["source_options"]["storage_allowed"] = True  # Synthetic fixtures only.
    rid = store.create(
        frozen["queries"],
        frozen["start"],
        frozen["end"],
        2,
        24,
        source=source,
        plan_snapshot=frozen,
    )
    first = collect(store, client(source, tmp_path, handler, max_requests=2), rid, max_details=10)
    assert first["status"] == "partial" and first["full_details"] == 1
    store.close()
    store = Store(tmp_path / "db.sqlite3")
    resumed = collect(store, client(source, tmp_path, handler), rid, max_details=10)
    assert resumed["status"] == "complete"
    assert resumed["full_details"] == resumed["distinct_vacancies"] == 3
    assert resumed["pages_saved"] == 2
    search_calls = [
        params for _, params in calls if any(k in params for k in ("keyword", "text", "q"))
    ]
    assert len(search_calls) == 2
    key = "offset" if source == "trudvsem" else "page"
    assert [int(p[key]) for p in search_calls] == ([1, 2] if source == "habr" else [0, 1])
    assert len(store.export(rid)["vacancies"]) == 3
    assert store.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
    store.close()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": "500"},
        {"status": "200", "meta": {"total": 1, "limit": 2}, "results": {}},
        {"status": "200", "meta": {"total": "1", "limit": 2}, "results": {"vacancies": []}},
    ],
)
def test_trudvsem_bad_envelope_is_not_empty_success(tmp_path, body):
    c = client("trudvsem", tmp_path, lambda _: httpx.Response(200, json=body))
    with pytest.raises(SourceError):
        c.search("test", START, END, 0, 2)


@pytest.mark.parametrize(
    "body",
    [
        "<html>challenge</html>",
        '<script type="application/ld+json">{}</script>',
        html_card(2),
        html_card().replace("data-ssr-state", "data-removed-state"),
    ],
)
def test_habr_drift_and_wrong_identity_never_become_full(body):
    with pytest.raises(SourceError):
        habr_detail(body, "1")


@pytest.mark.parametrize(
    "source,status,error",
    [
        ("habr", 403, "access_denied"),
        ("trudvsem", 429, "rate_limited"),
        ("superjob", 503, "rate_limited"),
        ("superjob", 401, "auth_required"),
    ],
)
def test_source_access_and_quota_errors_stop_without_retrying_or_leaking_key(
    tmp_path, source, status, error
):
    c = client(
        source,
        tmp_path,
        lambda _: httpx.Response(status, headers={"Retry-After": "20"}, text="fake-key"),
        retries=3,
        clock=lambda: 1000,
    )
    with pytest.raises(SourceError, match=error) as caught:
        c.search("test", START, END, 0, 2)
    assert c.used == 1
    assert "fake-key" not in str(caught.value)
    if error == "rate_limited":
        assert caught.value.retry_at == 1020


def test_superjob_storage_block_is_explicit_and_no_http_occurs(tmp_path):
    frozen = plan(load_config(CONFIG), source="superjob", queries=["test"])
    frozen["source_options"]["storage_allowed"] = False
    store = Store(tmp_path / "db.sqlite3")
    rid = store.create(
        frozen["queries"],
        frozen["start"],
        frozen["end"],
        100,
        24,
        source="superjob",
        plan_snapshot=frozen,
    )
    c = client("superjob", tmp_path, lambda _: pytest.fail("no source read with disabled storage"))
    report = collect(store, c, rid)
    assert report["status"] == "blocked" and report["error"] == "source_storage_policy_unconfirmed"
    assert c.used == 0 and not store.ready(rid)
    store.close()


def test_plan_all_offline_uses_source_specific_search_language_and_geo(tmp_path, capsys):
    assert cli.main(["--db", str(tmp_path / "unused.sqlite3"), "plan", "--source", "all"]) == 0
    plans = json.loads(capsys.readouterr().out)["plans"]
    assert len(plans) == 4
    assert all(len(p["queries"]) == (16 if p["source"] == "hh" else 12) for p in plans)
    assert not (tmp_path / "unused.sqlite3").exists()
    assert next(p for p in plans if p["source"] == "habr")["window_field"] == "all_visible"
    assert all("area_ids" not in p["source_options"] for p in plans if p["source"] != "hh")


def test_superjob_promoted_rows_do_not_change_native_stride(tmp_path):
    params = []

    def handler(req):
        params.append(dict(req.url.params))
        return httpx.Response(
            200, json={"objects": [sj(1), sj(2), sj(3)], "total": 100, "more": True}
        )

    c = client("superjob", tmp_path, handler)
    page = c.search("test", START, END, 0, 2)
    assert page.per_page == 2 and len(page.items) == 3 and page.has_more
    assert params[0]["count"] == "2"
    page = c.search("test", START, END, 0, 100)
    assert page.per_page == 40 and params[1]["count"] == "40"


def test_native_page_size_change_is_rejected_without_advancing_cursor(tmp_path):
    from career_scout.source_types import Page

    store = Store(tmp_path / "db.sqlite3")
    rid = store.create(["test"], START, END, 100, 24, source="habr")
    query = store.next_query(rid)
    store.save_page(
        query,
        Page(
            items=[{"id": "1"}],
            found=60,
            pages=3,
            page=0,
            per_page=25,
            has_more=True,
            window_cap=None,
        ),
        24,
    )
    assert store.page_size(query["id"], 100) == 25
    before = store.next_query(rid)["next_page"]
    with pytest.raises(SourceError, match="pagination_size_changed"):
        store.save_page(
            store.next_query(rid),
            Page(
                items=[{"id": "2"}],
                found=60,
                pages=6,
                page=1,
                per_page=10,
                has_more=True,
                window_cap=None,
            ),
            24,
        )
    assert store.next_query(rid)["next_page"] == before
    assert store.report(rid)["distinct_vacancies"] == 1
    store.close()


def test_superjob_cap_uses_persisted_date_windows_not_hh_2000_limit(tmp_path):
    calls = []

    def handler(req):
        params = req.url.params
        calls.append(dict(params))
        width = int(params["date_published_to"]) - int(params["date_published_from"])
        return httpx.Response(
            200,
            json={
                "objects": [] if width <= 2 else [sj()],
                "total": 0 if width <= 2 else 501,
                "more": width > 2,
            },
        )

    store = Store(tmp_path / "db.sqlite3")
    cfg = load_config(CONFIG)
    frozen = plan(cfg, source="superjob", queries=["test"], per_page=40)
    frozen.update(start="2026-11-11T00:00:00+00:00", end="2026-11-11T00:00:04+00:00")
    frozen["source_options"]["storage_allowed"] = True
    rid = store.create(
        ["test"], frozen["start"], frozen["end"], 40, 24, source="superjob", plan_snapshot=frozen
    )
    report = collect(store, client("superjob", tmp_path, handler), rid)
    assert report["status"] == "complete"
    assert len(calls) == 3
    assert calls[0]["date_published_from"] == calls[1]["date_published_from"]
    assert calls[1]["date_published_to"] == calls[2]["date_published_from"]
    assert store.db.execute("SELECT COUNT(*) FROM queries WHERE status='split'").fetchone()[0] == 1
    store.close()


def test_200_error_envelope_never_passes_for_vacancy(tmp_path):
    c = client(
        "superjob",
        tmp_path,
        lambda _: httpx.Response(200, json={"error": {"code": 403, "message": "fake-key"}}),
    )
    with pytest.raises(SourceError, match="access_denied"):
        c.detail("1")


@pytest.mark.parametrize("storage_allowed", [False, True])
def test_batch_continues_other_sources_and_resume_uses_saved_source(
    tmp_path, monkeypatch, capsys, storage_allowed
):
    from career_scout.source_types import Page

    seen = []

    class Empty:
        window_cap = None
        supports_date_split = False
        used = 0

        def __init__(self, source, fail=False):
            self.source = source
            self.fail = fail
            self.on_request = lambda: None

        def search(self, *args, **kw):
            seen.append(self.source)
            if self.fail:
                raise SourceError("access_denied")
            return Page(
                items=[], found=0, pages=0, page=0, per_page=100, has_more=False, window_cap=None
            )

        def clock(self):
            return 1000

        def close(self):
            pass

    monkeypatch.setattr(cli, "HHClient", lambda *a, **kw: Empty("hh"))
    monkeypatch.setattr(
        cli, "create_client", lambda source, *a, **kw: Empty(source, source == "habr")
    )
    path = tmp_path / "db.sqlite3"
    config = tmp_path / "search.toml"
    config.write_text(
        CONFIG.read_text().replace(
            "storage_allowed = true", "storage_allowed = " + str(storage_allowed).lower()
        )
    )
    assert (
        cli.main(
            [
                "--db",
                str(path),
                "--config",
                str(config),
                "collect",
                "--source",
                "all",
                "--query",
                "test",
            ]
        )
        == 2
    )
    out = json.loads(capsys.readouterr().out)
    reports = {r["plan"]["source"]: r for r in out["runs"]}
    assert reports["hh"]["status"] == reports["trudvsem"]["status"] == "complete"
    assert reports["habr"]["error"] == "access_denied"
    if storage_allowed:
        assert reports["superjob"]["status"] == "complete"
        assert reports["superjob"]["error"] is None
    else:
        assert reports["superjob"]["error"] == "source_storage_policy_unconfirmed"
    assert set(seen) == {"hh", "trudvsem", "habr"} | ({"superjob"} if storage_allowed else set())
    monkeypatch.setattr(cli, "create_client", lambda source, *a, **kw: Empty(source))
    assert (
        cli.main(
            [
                "--db",
                str(path),
                "--config",
                str(tmp_path / "missing.toml"),
                "resume",
                reports["habr"]["run_id"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["plan"]["source"] == "habr"
    assert seen[-1] == "habr"


def test_probe_does_not_create_database_or_store_superjob_payload(tmp_path, monkeypatch, capsys):
    def make(source, *args, **kwargs):
        return client(
            source,
            tmp_path,
            lambda req: httpx.Response(
                200,
                json=(listing(source, 0, [1], total=1) if "keyword" in req.url.params else sj()),
            ),
        )

    monkeypatch.setattr(cli, "create_client", make)
    path = tmp_path / "unused" / "db.sqlite3"
    assert cli.main(["--db", str(path), "probe", "--source", "superjob"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["storage"] == "none" and result["sources"][0]["detail_checked"]
    assert "source_payload" not in json.dumps(result)
    assert not path.parent.exists()


@pytest.mark.parametrize("source", ["trudvsem", "habr", "superjob"])
def test_seventy_five_full_cards_across_three_pages_and_restart(source, tmp_path):
    seen = []

    def handler(req):
        if any(k in req.url.params for k in ("q", "text", "keyword")):
            page = int(req.url.params["offset" if source == "trudvsem" else "page"]) - (
                source == "habr"
            )
            return httpx.Response(
                200,
                json=listing(
                    source, page, list(range(page * 25 + 1, page * 25 + 26)), total=75, per_page=25
                ),
            )
        vid = req.url.path.rstrip("/").split("/")[-1]
        assert vid not in seen
        seen.append(vid)
        if source == "habr":
            return httpx.Response(200, text=html_card(int(vid)))
        if source == "superjob":
            return httpx.Response(200, json=sj(int(vid)))
        return httpx.Response(
            200,
            json={
                "status": "200",
                "meta": {"total": 1, "limit": 100},
                "results": {"vacancies": [{"vacancy": trud(vid)}]},
            },
        )

    store = Store(tmp_path / "db.sqlite3")
    frozen = plan(load_config(CONFIG), source=source, queries=["test"], per_page=25)
    frozen["source_options"]["storage_allowed"] = True
    rid = store.create(
        ["test"], frozen["start"], frozen["end"], 25, 24, source=source, plan_snapshot=frozen
    )
    assert (
        collect(store, client(source, tmp_path, handler, max_requests=11), rid, max_details=100)[
            "full_details"
        ]
        == 10
    )
    store.close()
    store = Store(tmp_path / "db.sqlite3")
    final = collect(store, client(source, tmp_path, handler, max_requests=80), rid, max_details=100)
    assert final["full_details"] == final["distinct_vacancies"] == 75
    assert final["pages_saved"] == 3 and final["requests"] == 78 and final["status"] == "complete"
    assert len(store.export(rid, include_excluded=True)["vacancies"]) == 75
    assert len(seen) == len(set(seen)) == 75
    store.close()


@pytest.mark.parametrize(
    "transform",
    [
        lambda raw: raw.update(duty={"changed": "schema"}),
        lambda raw: raw["requirement"].update(qualification=["changed"]),
    ],
)
def test_trudvsem_critical_section_type_change_does_not_silently_drop_text(transform):
    raw = trud()
    transform(raw)
    with pytest.raises(SourceError, match="detail_schema_drift"):
        trudvsem_record(raw, source_identity("trudvsem", "1", company_code="company").source_id)


def test_malformed_optional_habr_grade_is_unknown_not_a_crash():
    card = habr_detail(html_card(qualification={"unexpected": "object"}), "1")
    assert evaluate(card, load_config(CONFIG).filters).criteria["grade"].status == "unknown"


@pytest.mark.parametrize("source,raw", [("habr", habr()), ("trudvsem", trud()), ("superjob", sj())])
def test_volatile_listing_metadata_does_not_force_refetch_but_conditions_do(source, raw):
    from career_scout.source_normalization import listing_hash

    original = listing_hash(source, raw)
    changed = __import__("copy").deepcopy(raw)
    changed.update(
        views_count=10000, reactions={"count": 99}, favorite=True, contact_person="Synthetic"
    )
    if isinstance(changed.get("company"), dict):
        changed["company"]["rating"] = 100
    assert listing_hash(source, changed) == original
    field = {"habr": "qualification", "trudvsem": "requirements", "superjob": "candidat"}[source]
    changed[field] = "Changed mandatory condition"
    assert listing_hash(source, changed) != original


def test_habr_negated_title_is_conflict_even_with_explicit_higher_api_grade():
    card = habr_detail(html_card(title="Аналитик, не Senior", qualification="Senior"), "1")
    result = evaluate(card, load_config(CONFIG).filters)
    assert result.criteria["grade"].status == "conflict"
    assert result.status == "KEEP_UNKNOWN"
    assert any(
        e.field == "title" and e.quote == "не Senior" for e in result.criteria["grade"].evidence
    )


def test_explicit_interrupt_stops_batch_before_other_sources(tmp_path, monkeypatch, capsys):
    def interrupted(store, run_id, plan, args, options):
        return store.finish(run_id, SourceError("interrupted"))

    monkeypatch.setattr(cli, "run_source", interrupted)
    assert (
        cli.main(
            ["--db", str(tmp_path / "db.sqlite3"), "collect", "--source", "all", "--query", "test"]
        )
        == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert len(report["runs"]) == 1 and report["runs"][0]["error"] == "interrupted"
    assert report["not_started_sources"] == ["trudvsem", "habr", "superjob"]


def test_trudvsem_uses_smaller_native_pages_without_a_total_result_quota(tmp_path):
    params = []

    def handler(req):
        params.append(dict(req.url.params))
        return httpx.Response(
            200,
            json={
                "status": "200",
                "meta": {"total": 1000, "limit": 20},
                "results": {"vacancies": [{"vacancy": trud(str(i))} for i in range(20)]},
            },
        )

    c = client("trudvsem", tmp_path, handler)
    result = c.search("test", None, END, 0, 100)
    assert params[0]["limit"] == "20"
    assert params[0]["modifiedFrom"].startswith("1970-01-01")
    assert result.per_page == 20 and result.pages == 50 and result.has_more
