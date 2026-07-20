from meshpi.config import Settings
from meshpi.doctor import offline_checks


def test_offline_doctor_does_not_require_meshtastic_node(tmp_path):
    settings = Settings(
        database_path=tmp_path / "unreachable" / "meshpi.db",
        connections_path=tmp_path / "unreachable" / "connections.json",
    )
    checks = offline_checks(settings)
    assert checks
    assert all(ok for _name, ok, _detail in checks)
