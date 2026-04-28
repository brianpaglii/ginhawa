import ginhawa_kiosk


def test_package_exposes_version() -> None:
    assert hasattr(ginhawa_kiosk, "__version__")
    assert isinstance(ginhawa_kiosk.__version__, str)
    assert ginhawa_kiosk.__version__ == "0.0.0"
