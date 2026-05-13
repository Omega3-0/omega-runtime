from __future__ import annotations

from omega_studio.downloads.hub_jobs import HubJobStore


def test_hub_job_store_persists_jobs_to_sqlite(tmp_path) -> None:
    db = tmp_path / "hub_jobs.sqlite"
    store = HubJobStore(db)
    job = store.create()
    job.status = "done"
    job.result_path = r"C:\models\m.gguf"
    job.push(pct=1.0, msg="complete")
    store.save(job)

    reloaded = HubJobStore(db)
    got = reloaded.get(job.job_id)

    assert got is not None
    assert got.status == "done"
    assert got.result_path == r"C:\models\m.gguf"
    assert got.to_public()["recent_events"][-1]["message"] == "complete"
