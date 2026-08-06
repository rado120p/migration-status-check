"""Parovani pre/post/rollback snimku - cista funkce nad manifestem."""

from migration_validator.runs.manifest import (
    CaptureRecord,
    InterfaceMapping,
    MappingEndpoint,
    RunDevice,
    RunManifest,
)
from migration_validator.runs.pairing import plan_evaluations


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
