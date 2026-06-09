from mcp_xray.counting import OfflineCounter
from mcp_xray.probes.base import RunContext
from mcp_xray.probes.noise import NoiseProbe


class FakeClient:
    """Deterministic ChatClient stand-in: always 'picks' a fixed tool (or None),
    letting us exercise the probe logic without a real API."""

    backend = "fake"
    model = "fake-model"

    def __init__(self, pick):
        self._pick = pick

    def pick_tool(self, tools, system, query, *, allow_none):
        return self._pick


def _ctx(inv, client, queries=None):
    return RunContext(
        inventory=inv,
        counter=OfflineCounter(),
        available={"inventory", "api_key", "llm"},
        model="claude-x",
        client=client,
        queries=queries,
        config={"noise_samples": 2},
    )


def test_noise_requires_llm(crud_inventory):
    probe = NoiseProbe()
    ctx = RunContext(inventory=crud_inventory, counter=OfflineCounter(), available={"inventory"})
    assert not probe.can_run(ctx)
    assert probe.missing(ctx) == {"api_key", "llm"}


def test_distraction_flags_when_tool_fires(crud_inventory):
    # Model always fires create_label, even on off-domain tasks -> distraction.
    findings = NoiseProbe().run(_ctx(crud_inventory, FakeClient("create_label")))
    distr = [f for f in findings if f.kind == "distraction"]
    assert distr
    assert all(f.measurement["fire_rate"] > 0 for f in distr)


def test_no_distraction_when_model_declines(crud_inventory):
    findings = NoiseProbe().run(_ctx(crud_inventory, FakeClient(None)))
    assert [f for f in findings if f.kind == "distraction"] == []


def test_confusability_proxy_offpick(crud_inventory):
    # Model always picks create_label; every other tool's probe query mis-picks.
    findings = NoiseProbe().run(_ctx(crud_inventory, FakeClient("create_label")))
    proxy = [f for f in findings if f.kind == "selection_error" and f.measurement["mode"] == "confusability_proxy"]
    # create_label itself is not an error; all others are.
    assert len(proxy) == len(crud_inventory.tools) - 1


def test_labeled_selection(crud_inventory):
    queries = [{"query": "make a new label", "expected_tools": ["create_label"]}]
    findings = NoiseProbe().run(_ctx(crud_inventory, FakeClient("create_label"), queries=queries))
    labeled = [f for f in findings if f.kind == "selection_error" and f.measurement["mode"] == "labeled"]
    assert labeled
    assert labeled[0].measurement["pass_rate"] == 1.0


class RaisingClient:
    """ChatClient whose API call always fails - simulates rate limit / auth /
    bad-schema errors that must NOT be scored as model behavior."""

    backend = "fake"
    model = "fake-model"

    def pick_tool(self, tools, system, query, *, allow_none):
        raise RuntimeError("429 rate limited")


def test_api_errors_not_scored_as_misses(crud_inventory):
    # Every sample errors -> no selection_error/distraction findings fabricated
    # (a failed call is not a wrong pick). The grade must not be corrupted.
    queries = [{"query": "make a new label", "expected_tools": ["create_label"]}]
    findings = NoiseProbe().run(_ctx(crud_inventory, RaisingClient(), queries=queries))
    assert [f for f in findings if f.kind in ("selection_error", "distraction")] == []


def test_partial_errors_scored_over_successful_only(crud_inventory):
    # 1st call errors, rest pick the expected tool -> pass_rate from successes
    # only (1.0), with the error count recorded for transparency.
    class FlakyThenOK:
        backend = "fake"
        model = "fake-model"

        def __init__(self):
            self.n = 0

        def pick_tool(self, tools, system, query, *, allow_none):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("transient")
            return "create_label"

    queries = [{"query": "make a new label", "expected_tools": ["create_label"]}]
    findings = NoiseProbe().run(_ctx(crud_inventory, FlakyThenOK(), queries=queries))
    labeled = [f for f in findings if f.measurement.get("mode") == "labeled"]
    assert labeled
    m = labeled[0].measurement
    assert m["pass_rate"] == 1.0          # the error wasn't counted as a miss
    assert m["errors"] == 1 and m["samples"] == 1  # 2 samples, 1 errored, 1 ok


def test_resume_cache_reuses_completed_samples(crud_inventory, tmp_path):
    # Simulate a credit-out mid-run, then resume: completed samples are cached
    # and reused, so the second run only pays for the remainder.
    cache = tmp_path / "probe-cache.jsonl"
    N = 2

    class FailAfter:
        backend = "fake"
        model = "m"

        def __init__(self, ok):
            self.ok = ok
            self.calls = 0

        def pick_tool(self, tools, system, query, *, allow_none):
            self.calls += 1
            if self.calls <= self.ok:
                return "create_label"
            raise RuntimeError("credit balance too low")

    def ctx(client):
        return RunContext(
            inventory=crud_inventory, counter=OfflineCounter(),
            available={"inventory", "api_key", "llm"}, model="m", client=client,
            queries=[{"query": "x", "expected_tools": ["create_label"]}],
            config={"noise_samples": N, "probe_cache": str(cache)},
        )

    total = (1 + len(__import__("mcp_xray.probes.noise", fromlist=["DISTRACTION_TASKS"]).DISTRACTION_TASKS)) * N

    # Round 1: only the first 3 calls succeed (cached); the rest error out.
    c1 = FailAfter(ok=3)
    NoiseProbe().run(ctx(c1))
    assert c1.calls == total                       # attempted everything
    assert sum(1 for _ in open(cache)) == 3        # 3 successful samples persisted

    # Round 2: a working client; cached samples reused, only remainder called.
    c2 = FailAfter(ok=10**9)
    findings = NoiseProbe().run(ctx(c2))
    assert c2.calls == total - 3                   # did NOT re-pay for the 3 cached
    lab = [f for f in findings if f.measurement.get("mode") == "labeled"][0]
    assert lab.measurement["errors"] == 0          # fully resolved on resume
