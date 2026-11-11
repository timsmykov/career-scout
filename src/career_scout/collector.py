"""Synchronous, restartable HH collection. No model or agent dependencies."""

import json

from career_scout.hh import HHClient, SourceError
from career_scout.storage import Store


def collect(store: Store, client: HHClient, run_id: str, *, max_details: int = 30) -> dict:
    with store.lock():
        run = store.run(run_id)
        cooldown = store.cooldown(run["source"])
        if cooldown and cooldown > client.clock():
            return store.finish(run_id, SourceError("rate_limited", cooldown))
        store.begin(run_id)
        client.on_request = lambda: store.reserve_request(run_id)
        plan = json.loads(run["plan_json"])
        error = None
        details = 0
        try:
            if run["source"] != "hh":
                raise SourceError("source_adapter_unavailable")
            while True:
                # Drain existing pending detail before fetching the next page. Its state survives
                # a crash, so a committed page is never skipped just because its details failed.
                pending = store.pending_detail(run_id)
                if pending and details < max_details:
                    try:
                        snapshot = client.detail(pending["source_id"])
                    except SourceError as exc:
                        if exc.code != "unavailable":
                            raise
                        store.unavailable(pending["source_id"], pending["source"])
                    else:
                        store.save_detail(snapshot)
                    details += 1
                    continue
                query = store.next_query(run_id)
                if query is None:
                    break
                page = client.search(
                    query["query"],
                    query["start"],
                    query["end"],
                    query["next_page"],
                    plan["per_page"],
                    area_ids=plan.get("source_options", {}).get("area_ids", []),
                )
                if page.found > 2000 and query["next_page"] == 0 and store.split(query, page.found):
                    continue
                store.save_page(query, page, plan["ttl_hours"])
        except SourceError as exc:
            error = exc
        except KeyboardInterrupt:
            error = SourceError("interrupted")
        store.filter_run(run_id)
        return store.finish(run_id, error)
