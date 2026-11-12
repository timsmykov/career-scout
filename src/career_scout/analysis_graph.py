"""Code-owned first-PASS routing; each model sees one isolated WorkerInput."""

import sqlite3
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from career_scout.evaluation import ORDER, EvaluationError, EvaluationResult, validate_result


class RouteState(TypedDict, total=False):
    index: int
    outcomes: dict
    status: str
    accepted_profile: str | None
    skipped: list[str]


class AnalysisGraph:
    def __init__(self, store, worker, config, *, order=ORDER, order_confirmed=False):
        if len(order) != 4 or set(order) != set(ORDER):
            raise EvaluationError("invalid_profile_order", fatal=True)
        if not order_confirmed:
            raise EvaluationError("profile_order_not_confirmed", fatal=True)
        self.store = store
        self.worker = worker
        self.config = config
        self.order = tuple(order)

    def evaluate(self, run_id, route_key):
        requests = self.store.inputs(route_key)
        if tuple(r.profile_id for r in requests) != self.order:
            raise EvaluationError("job_order_mismatch", fatal=True)
        if any(r.model_config_hash != self.config.key() for r in requests):
            raise EvaluationError("model_config_mismatch", fatal=True)
        saved_config = self.store.run(run_id)
        generation = saved_config["retry_generation"]

        def evaluate_profile(state):
            index = state["index"]
            request = requests[index]
            result = self.store.cached(request)
            cached = result is not None
            errors = []
            while (
                result is None
                and self.store.attempt_count(run_id, request.evaluation_key)
                < self.config.max_attempts_per_profile
            ):
                attempt_id = self.store.reserve(run_id, request.evaluation_key)
                reply = None
                try:
                    reply = self.worker(request)
                    result = validate_result(request, EvaluationResult.model_validate(reply.result))
                    result = self.store.save_result(request, result)
                except EvaluationError as exc:
                    result = None
                    self.store.finish_attempt(
                        attempt_id,
                        error=exc.code,
                        usage=reply.usage if reply else exc.usage,
                        response=reply.result if reply else None,
                    )
                    if exc.fatal:
                        raise
                    errors.append(exc.code)
                except ValidationError:
                    result = None
                    self.store.finish_attempt(
                        attempt_id,
                        error="invalid_evaluation_result",
                        usage=reply.usage if reply else None,
                        response=reply.result if reply else None,
                    )
                    errors.append("invalid_evaluation_result")
                else:
                    self.store.finish_attempt(attempt_id, usage=reply.usage, response=reply.result)
            outcome = {
                "verdict": result.verdict if result else "ERROR",
                "evaluation_key": request.evaluation_key,
                "cached": cached,
            }
            if errors:
                outcome["errors"] = errors
            outcomes = {**state.get("outcomes", {}), request.profile_id: outcome}
            return {"index": index + 1, "outcomes": outcomes}

        def next_step(state):
            if any(o["verdict"] == "PASS" for o in state["outcomes"].values()):
                return "finish"
            return "finish" if state["index"] == 4 else "evaluate_profile"

        def finish(state):
            outcomes = state["outcomes"]
            accepted = next(
                (p for p in self.order if outcomes.get(p, {}).get("verdict") == "PASS"), None
            )
            verdicts = [outcomes.get(p, {}).get("verdict") for p in self.order]
            if accepted:
                status = "ACCEPTED"
            elif "ERROR" in verdicts or None in verdicts:
                status = "PENDING"
            elif all(v == "REJECT" for v in verdicts):
                status = "REJECTED"
            else:
                status = "NEEDS_CLARIFICATION"
            skipped = list(self.order[state["index"] :]) if accepted else []
            for profile in skipped:
                outcomes = {**outcomes, profile: {"verdict": "SKIPPED_AFTER_ACCEPT"}}
            final = {
                "status": status,
                "accepted_profile": accepted,
                "skipped": skipped,
                "outcomes": outcomes,
                "index": state["index"],
            }
            self.store.save_outcome(run_id, route_key, final)
            return final

        builder = StateGraph(RouteState)
        builder.add_node("evaluate_profile", evaluate_profile)
        builder.add_node("finish", finish)
        builder.add_edge(START, "evaluate_profile")
        builder.add_conditional_edges(
            "evaluate_profile",
            next_step,
            {"evaluate_profile": "evaluate_profile", "finish": "finish"},
        )
        builder.add_edge("finish", END)
        # LangGraph flushes checkpoints on a background thread. Sharing the
        # ledger connection lets either transaction commit the other's work.
        # Separate connections use SQLite's own transaction serialization.
        checkpoint_db = sqlite3.connect(self.store.path, check_same_thread=False)
        try:
            graph = builder.compile(checkpointer=SqliteSaver(checkpoint_db))
            checkpoint_config = {
                "configurable": {"thread_id": f"{run_id}:{route_key}:{generation}"}
            }
            checkpoint = graph.get_state(checkpoint_config)
            if checkpoint.values and not checkpoint.next:
                return checkpoint.values
            return graph.invoke(
                None if checkpoint.values else {"index": 0, "outcomes": {}}, checkpoint_config
            )
        finally:
            checkpoint_db.close()
