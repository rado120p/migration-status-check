"""Parovani pre/post/rollback snimku pro evaluate --run (faze 4 roadmapy).

Cista funkce nad RunManifest - zadne IO, zadne nacitani snapshotu.
"""

from __future__ import annotations

from dataclasses import dataclass

from migration_validator.runs.manifest import (
    CaptureRecord,
    InterfaceMapping,
    RunManifest,
)


@dataclass
class Evaluation:
    subject: CaptureRecord
    baseline: CaptureRecord | None
    reason: str | None = None
    # Migracni krok (zaznam interface_mapping), ktery evaluaci vyrobil.
    # None = celoboxova nebo nemapovana evaluace - chovani beze zmeny.
    step: InterfaceMapping | None = None
    # True = post vs pre stejneho zarizeni navic k mapovanym evaluacim
    # (box rekonfigurovany na miste); GUI je vykresluje v samostatne sekci.
    same_device: bool = False


def _passes_port_filter(port: str | None, ports: list[str] | None) -> bool:
    if not ports:
        return True
    if port is None:
        return False
    return port in ports


def find_pre_baseline(
    manifest: RunManifest, node: str, port: str | None
) -> CaptureRecord | None:
    """Najde pre snimek stareho boxu pro dany node/port.

    Nejdriv zkusi per-port parovani pres interface_mapping, pak spadne na
    celoboxovy pre snimek stareho boxu (role "old"). Sdileno mezi
    plan_evaluations (evaluate --run) a cli._capture_into_run (baseline pro
    ping cile pri post capture).
    """
    baseline: CaptureRecord | None = None

    if port is not None:
        old_endpoint = manifest.paired_old(node, port)
        if old_endpoint is not None:
            baseline = manifest.find_capture("pre", old_endpoint.node, old_endpoint.port)

    if baseline is None:
        old = manifest.device_with_role("old")
        if old is not None:
            old_node, _ = old
            baseline = manifest.find_capture("pre", old_node, None)

    return baseline


def _plan_post(manifest: RunManifest, subject: CaptureRecord) -> list[Evaluation]:
    steps = [
        mapping
        for mapping in manifest.interface_mapping
        if subject.port is not None
        and mapping.new.node == subject.device
        and mapping.new.port == subject.port
    ]

    if not steps:
        baseline = find_pre_baseline(manifest, subject.device, subject.port)
        if baseline is None:
            return [
                Evaluation(
                    subject=subject,
                    baseline=None,
                    reason="chybi pre snimek stareho boxu",
                )
            ]
        return [Evaluation(subject=subject, baseline=baseline)]

    evaluations: list[Evaluation] = []
    for mapping in steps:
        baseline = manifest.find_capture(
            "pre", mapping.old.node, mapping.old.port
        ) or manifest.find_capture("pre", mapping.old.node, None)
        if baseline is None:
            evaluations.append(
                Evaluation(
                    subject=subject,
                    baseline=None,
                    reason=(
                        "chybi pre snimek "
                        f"{mapping.old.node}:{mapping.old.port}"
                    ),
                    step=mapping,
                )
            )
        else:
            evaluations.append(
                Evaluation(subject=subject, baseline=baseline, step=mapping)
            )
    return evaluations


def _plan_rollback(manifest: RunManifest, subject: CaptureRecord) -> Evaluation:
    baseline = manifest.find_capture("pre", subject.device, subject.port)
    if baseline is None:
        return Evaluation(
            subject=subject,
            baseline=None,
            reason="chybi puvodni pre snimek stejneho zarizeni a portu",
        )
    return Evaluation(subject=subject, baseline=baseline)


def _plan_same_device(
    manifest: RunManifest, planned: list[Evaluation]
) -> list[Evaluation]:
    """Post vs pre stejneho zarizeni a portu - navic k mapovanym evaluacim.

    Pokryva box s vlastnim pre i post (rekonfigurace na miste). Dvojice,
    kterou uz vyrobilo mapovane/fallback planovani (stary box s celoboxovym
    pre), se neduplikuje.
    """
    existing = {
        (e.subject.snapshot, e.baseline.snapshot)
        for e in planned
        if e.baseline is not None
    }
    evaluations: list[Evaluation] = []
    for capture in manifest.captures:
        if capture.phase != "post":
            continue
        baseline = manifest.find_capture("pre", capture.device, capture.port)
        if baseline is None:
            continue
        if (capture.snapshot, baseline.snapshot) in existing:
            continue
        evaluations.append(
            Evaluation(subject=capture, baseline=baseline, same_device=True)
        )
    return evaluations


def _filter_port(evaluation: Evaluation) -> str | None:
    if evaluation.step is not None:
        return evaluation.step.old.port
    return evaluation.subject.port


def plan_evaluations(
    manifest: RunManifest, ports: list[str] | None = None
) -> list[Evaluation]:
    """Naplanuje evaluace pro capturey s fazi post/rollback.

    pre captury samy o sobe evaluaci netvori - jsou jen baseline zdroj.
    Post snimek portu s vice mapovanymi old porty vyrobi evaluaci na kazdy
    mapping (migracni krok); `ports` filtr se u kroku vztahuje na stary port.
    """
    evaluations: list[Evaluation] = []
    for capture in manifest.captures:
        if capture.phase not in ("post", "rollback"):
            continue
        if capture.phase == "post":
            evaluations.extend(_plan_post(manifest, capture))
        else:
            evaluations.append(_plan_rollback(manifest, capture))
    evaluations.extend(_plan_same_device(manifest, evaluations))
    return [
        evaluation
        for evaluation in evaluations
        if _passes_port_filter(_filter_port(evaluation), ports)
    ]
