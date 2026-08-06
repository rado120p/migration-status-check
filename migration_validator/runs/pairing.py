"""Parovani pre/post/rollback snimku pro evaluate --run (faze 4 roadmapy).

Cista funkce nad RunManifest - zadne IO, zadne nacitani snapshotu.
"""

from __future__ import annotations

from dataclasses import dataclass

from migration_validator.runs.manifest import CaptureRecord, RunManifest


@dataclass
class Evaluation:
    subject: CaptureRecord
    baseline: CaptureRecord | None
    reason: str | None = None


def _passes_port_filter(port: str | None, ports: list[str] | None) -> bool:
    if not ports:
        return True
    if port is None:
        return False
    return port in ports


def _plan_post(manifest: RunManifest, subject: CaptureRecord) -> Evaluation:
    baseline: CaptureRecord | None = None

    if subject.port is not None:
        old_endpoint = manifest.paired_old(subject.device, subject.port)
        if old_endpoint is not None:
            baseline = manifest.find_capture("pre", old_endpoint.node, old_endpoint.port)

    if baseline is None:
        old = manifest.device_with_role("old")
        if old is not None:
            old_node, _ = old
            baseline = manifest.find_capture("pre", old_node, None)

    if baseline is None:
        return Evaluation(
            subject=subject, baseline=None, reason="chybi pre snimek stareho boxu"
        )
    return Evaluation(subject=subject, baseline=baseline)


def _plan_rollback(manifest: RunManifest, subject: CaptureRecord) -> Evaluation:
    baseline = manifest.find_capture("pre", subject.device, subject.port)
    if baseline is None:
        return Evaluation(
            subject=subject,
            baseline=None,
            reason="chybi puvodni pre snimek stejneho zarizeni a portu",
        )
    return Evaluation(subject=subject, baseline=baseline)


def plan_evaluations(
    manifest: RunManifest, ports: list[str] | None = None
) -> list[Evaluation]:
    """Naplanuje evaluace pro capturey s fazi post/rollback.

    pre captury samy o sobe evaluaci netvori - jsou jen baseline zdroj.
    """
    evaluations: list[Evaluation] = []
    for capture in manifest.captures:
        if capture.phase not in ("post", "rollback"):
            continue
        if not _passes_port_filter(capture.port, ports):
            continue
        if capture.phase == "post":
            evaluations.append(_plan_post(manifest, capture))
        else:
            evaluations.append(_plan_rollback(manifest, capture))
    return evaluations
