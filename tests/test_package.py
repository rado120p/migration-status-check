import migration_validator


def test_package_exposes_version():
    assert migration_validator.__version__ == "0.1.0"
