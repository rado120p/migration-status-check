"""Parovani pre/post/rollback snimku - cista funkce nad manifestem."""

from migration_validator.runs.manifest import (
    CaptureRecord,
    InterfaceMapping,
    MappingEndpoint,
    RunDevice,
    RunManifest,
)
from migration_validator.runs.pairing import find_pre_baseline, plan_evaluations


def _manifest():
    return RunManifest(
        devices={
            "MX1-POP1": RunDevice(host="172.20.20.4", platform="junos", role="old"),
            "PTX1-POP1": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="new"
            ),
        },
        interface_mapping=[
            InterfaceMapping(
                old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/0"),
                new=MappingEndpoint(node="PTX1-POP1", port="et-0/0/0"),
            )
        ],
        captures=[],
    )


def test_post_pairs_with_per_port_pre():
    manifest = _manifest()
    pre = CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "pre.json", "T1")
    post = CaptureRecord("post", "PTX1-POP1", "et-0/0/0", "post.json", "T2")
    manifest.captures = [pre, post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    evaluation = evaluations[0]
    assert evaluation.subject == post
    assert evaluation.baseline == pre
    assert evaluation.reason is None


def test_post_falls_back_to_whole_box_pre():
    manifest = _manifest()
    pre_all = CaptureRecord("pre", "MX1-POP1", None, "pre_all.json", "T1")
    post = CaptureRecord("post", "PTX1-POP1", "et-0/0/0", "post.json", "T2")
    manifest.captures = [pre_all, post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    evaluation = evaluations[0]
    assert evaluation.subject == post
    assert evaluation.baseline == pre_all
    assert evaluation.reason is None


def test_post_without_any_pre_has_reason():
    manifest = _manifest()
    post = CaptureRecord("post", "PTX1-POP1", "et-0/0/0", "post.json", "T2")
    manifest.captures = [post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    evaluation = evaluations[0]
    assert evaluation.baseline is None
    assert evaluation.reason == "chybi pre snimek stareho boxu"


def test_rollback_pairs_with_pre_of_same_device_and_port():
    manifest = _manifest()
    pre = CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "pre.json", "T1")
    rollback = CaptureRecord("rollback", "MX1-POP1", "ge-0/0/0", "rb.json", "T3")
    manifest.captures = [pre, rollback]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    evaluation = evaluations[0]
    assert evaluation.subject == rollback
    assert evaluation.baseline == pre
    assert evaluation.reason is None


def test_rollback_without_pre_has_reason():
    manifest = _manifest()
    rollback = CaptureRecord("rollback", "MX1-POP1", "ge-0/0/0", "rb.json", "T3")
    manifest.captures = [rollback]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    assert evaluations[0].baseline is None
    assert evaluations[0].reason == (
        "chybi puvodni pre snimek stejneho zarizeni a portu"
    )


def test_ports_filter_keeps_only_matching_and_drops_whole_box():
    manifest = _manifest()
    pre = CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "pre.json", "T1")
    post_matching = CaptureRecord("post", "PTX1-POP1", "et-0/0/0", "post1.json", "T2")
    post_other = CaptureRecord("post", "PTX1-POP1", "et-0/0/1", "post2.json", "T2")
    post_whole_box = CaptureRecord("post", "PTX1-POP1", None, "post3.json", "T2")
    manifest.captures = [pre, post_matching, post_other, post_whole_box]

    evaluations = plan_evaluations(manifest, ports=["et-0/0/0"])

    assert len(evaluations) == 1
    assert evaluations[0].subject == post_matching


def test_pre_only_manifest_produces_empty_plan():
    manifest = _manifest()
    manifest.captures = [CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "pre.json", "T1")]

    assert plan_evaluations(manifest) == []


def _manifest_lag():
    """Dva stare UNI porty mapovane na jeden novy LAG port."""
    manifest = _manifest()
    manifest.interface_mapping = [
        InterfaceMapping(
            old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/4"),
            new=MappingEndpoint(node="PTX1-POP1", port="ae0"),
        ),
        InterfaceMapping(
            old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/5"),
            new=MappingEndpoint(node="PTX1-POP1", port="ae0"),
        ),
    ]
    return manifest


def test_post_on_shared_lag_yields_one_evaluation_per_mapping():
    manifest = _manifest_lag()
    pre4 = CaptureRecord("pre", "MX1-POP1", "ge-0/0/4", "pre4.json", "T1")
    pre5 = CaptureRecord("pre", "MX1-POP1", "ge-0/0/5", "pre5.json", "T2")
    post = CaptureRecord("post", "PTX1-POP1", "ae0", "post.json", "T3")
    manifest.captures = [pre4, pre5, post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 2
    assert [e.subject for e in evaluations] == [post, post]
    baselines = [e.baseline for e in evaluations]
    assert pre4 in baselines and pre5 in baselines
    steps = {(e.step.old.port, e.step.new.port) for e in evaluations}
    assert steps == {("ge-0/0/4", "ae0"), ("ge-0/0/5", "ae0")}
    assert all(e.reason is None for e in evaluations)


def test_mapped_step_without_per_port_pre_falls_back_to_whole_box():
    manifest = _manifest_lag()
    pre_all = CaptureRecord("pre", "MX1-POP1", None, "pre_all.json", "T1")
    post = CaptureRecord("post", "PTX1-POP1", "ae0", "post.json", "T2")
    manifest.captures = [pre_all, post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 2
    assert all(e.baseline == pre_all for e in evaluations)
    assert all(e.step is not None for e in evaluations)


def test_mapped_step_without_any_pre_has_reason():
    manifest = _manifest_lag()
    post = CaptureRecord("post", "PTX1-POP1", "ae0", "post.json", "T1")
    manifest.captures = [post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 2
    assert all(e.baseline is None for e in evaluations)
    assert "ge-0/0/4" in evaluations[0].reason
    assert "ge-0/0/5" in evaluations[1].reason


def test_ports_filter_selects_by_old_port_of_step():
    manifest = _manifest_lag()
    pre4 = CaptureRecord("pre", "MX1-POP1", "ge-0/0/4", "pre4.json", "T1")
    pre5 = CaptureRecord("pre", "MX1-POP1", "ge-0/0/5", "pre5.json", "T2")
    post = CaptureRecord("post", "PTX1-POP1", "ae0", "post.json", "T3")
    manifest.captures = [pre4, pre5, post]

    evaluations = plan_evaluations(manifest, ports=["ge-0/0/4"])

    assert len(evaluations) == 1
    assert evaluations[0].baseline == pre4


def test_unmapped_post_keeps_todays_fallback():
    # zadny mapping -> celoboxovy fallback, step zustava None (dnesni chovani)
    manifest = _manifest_lag()
    manifest.interface_mapping = []
    pre_all = CaptureRecord("pre", "MX1-POP1", None, "pre_all.json", "T1")
    post = CaptureRecord("post", "PTX1-POP1", "et-0/0/0", "post.json", "T2")
    manifest.captures = [pre_all, post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    assert evaluations[0].baseline == pre_all
    assert evaluations[0].step is None


# --- find_pre_baseline (sdilena logika s cli._capture_into_run) -----------


def test_find_pre_baseline_uses_per_port_pairing():
    manifest = _manifest()
    pre = CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "pre.json", "T1")
    manifest.captures = [pre]

    assert find_pre_baseline(manifest, "PTX1-POP1", "et-0/0/0") == pre


def test_find_pre_baseline_falls_back_to_whole_box():
    manifest = _manifest()
    pre_all = CaptureRecord("pre", "MX1-POP1", None, "pre_all.json", "T1")
    manifest.captures = [pre_all]

    assert find_pre_baseline(manifest, "PTX1-POP1", "et-0/0/0") == pre_all


def test_find_pre_baseline_none_when_missing():
    manifest = _manifest()
    manifest.captures = []

    assert find_pre_baseline(manifest, "PTX1-POP1", "et-0/0/0") is None
