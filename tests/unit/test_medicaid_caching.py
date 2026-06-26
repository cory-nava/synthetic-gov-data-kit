from govsynth.sources.us.medicaid import MedicaidSource


def test_medicaid_caching():
    source = MedicaidSource(calendar_year=2026, state="VA")
    # This should exercise fetch_thresholds and cached FPL loading
    thresholds = source.fetch_thresholds()
    assert thresholds.program == "medicaid"
    assert thresholds.state == "VA"

    # This should exercise get_income_limit and cached FPL loading
    limit = source.get_income_limit("adult")
    assert limit is not None

    # This should exercise is_eligible and cached FPL loading
    eligible, reason = source.is_eligible(1, 1000, "adult")
    assert isinstance(eligible, bool)
