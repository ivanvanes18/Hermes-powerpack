"""Narrow isolation for unrelated foreign state.db holders in ordinary state tests.

Holder-detection tests are intentionally absent from this allowlist: they must exercise the
real /proc/process-holder implementation.
"""

import pytest


_FOREIGN_HOLDER_ISOLATED_FILES = {
    "test_auto_vacuum_in_process_holder_gate.py",
    "test_fts_fresh_bootstrap_admission.py",
    "test_fts_rebuild_admission.py",
    "test_fts_runtime_rebuild.py",
    "test_startup_maintenance_lease.py",
    "test_state_db_fts_segment_collision_probe.py",
    "test_state_db_malformed_repair.py",
    "test_state_db_repair_loop_cap.py",
    "test_state_db_repair_loop_mtime.py",
    "test_state_db_repair_non_destructive.py",
    "test_state_db_wal_unlink_race.py",
}

_FOREIGN_HOLDER_ISOLATED_TESTS = {
    "test_live_writer_probe_detects_real_holder",
    "test_linux_holder_scan_does_not_require_psutil",
    "test_repair_proceeds_once_the_database_is_quiescent",
    "TestVacuum::test_auto_maintenance_records_successful_vacuum",
    "TestVacuum::test_auto_maintenance_retries_after_vacuum_interval",
    "TestVacuum::test_auto_maintenance_retries_after_failed_vacuum",
    "TestVacuum::test_auto_maintenance_vacuums_above_freelist_ratio",
    "TestVacuum::test_auto_maintenance_unknown_freelist_ratio_falls_back_to_time_throttle",
    "TestVacuum::test_auto_maintenance_ratio_gate_threshold_is_overridable",
    "TestAutoMaintenance::test_first_run_prunes_and_vacuums_when_mostly_reclaimable",
    "TestConnectionLifecycle::test_failed_read_only_open_does_not_leak_tracked_connection",
}


@pytest.fixture(autouse=True)
def isolate_unrelated_foreign_state_db_holders(request, monkeypatch):
    file_name = request.path.name
    test_key = f"{request.node.cls.__name__}::" if request.node.cls else ""
    test_key += request.node.name
    if file_name == "test_fts_runtime_rebuild.py" and request.node.name.startswith("test_foreign_holder_"):
        return
    if file_name == "test_state_db_malformed_repair.py" and request.node.name == "test_two_processes_repairing_at_once_perform_surgery_once":
        return
    if file_name not in _FOREIGN_HOLDER_ISOLATED_FILES and test_key not in _FOREIGN_HOLDER_ISOLATED_TESTS:
        return

    import hermes_state_holders

    monkeypatch.setattr(hermes_state_holders, "foreign_state_db_holders", lambda _db_path: [])
