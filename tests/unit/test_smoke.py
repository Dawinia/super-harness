def test_package_importable():
    import super_harness

    assert super_harness.__version__ == "0.1.0"
